#!/usr/bin/env python3
"""Local S3 control adapter. All writes are validated and read back."""
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent / "vendor/yolocam-lib"))

NAMES = {
    "zoom": "Zoom", "focus-mode": "Focus mode", "focus-level": "Focus area",
    "exposure-mode": "Exposure", "exposure-ev": "Exposure compensation",
    "exposure-iso": "ISO", "exposure-time": "Shutter speed",
    "wb-mode": "White balance", "wb-temp": "Temperature",
    "brightness": "Brightness", "contrast": "Contrast", "saturation": "Saturation",
    "sharpness": "Sharpness", "anti-flicker": "Anti-flicker", "hdr": "HDR",
}
UVC_NAMES = {"zoom_absolute": "Zoom", "white_balance_automatic": "Automatic white balance",
             "white_balance_temperature": "Temperature", "contrast": "Contrast",
             "saturation": "Saturation", "sharpness": "Sharpness",
             "power_line_frequency": "Anti-flicker"}


def command(*args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=6)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "Camera request failed")
    return p.stdout


def device():
    for node in sorted(Path("/sys/class/video4linux").glob("video*")):
        if node.joinpath("index").read_text().strip() != "0":
            continue
        for ancestor in node.resolve().parents:
            vid, pid = ancestor / "idVendor", ancestor / "idProduct"
            if vid.exists() and pid.exists():
                if vid.read_text().strip() == "46f8" and pid.read_text().strip() == "0855":
                    return "/dev/" + node.name
                break
    raise RuntimeError("YoloCam S3 is disconnected. Connect it by USB, then refresh.")


def uvc_controls(dev):
    result = []
    current = None
    for line in command("v4l2-ctl", "-d", dev, "--list-ctrls-menus").splitlines():
        match = re.match(r"\s*(\w+) 0x[0-9a-f]+ \((\w+)\)\s*:\s*(.*)", line)
        if match:
            name, kind, info = match.groups()
            current = None
            if name not in UVC_NAMES:
                continue
            nums = {k: int(v) for k, v in re.findall(r"(min|max|step|value)=(-?\d+)", info)}
            if "value" not in nums:
                continue
            current = dict(id=name, label=UVC_NAMES[name], kind="choice" if kind in ("bool", "menu") else "slider",
                           minimum=nums.get("min", 0), maximum=nums.get("max", 1), step=nums.get("step", 1),
                           value=nums["value"], writable=not any(x in info for x in ("inactive", "read-only", "disabled")),
                           options=[{"value": 0, "label": "Off"}, {"value": 1, "label": "On"}] if kind == "bool" else [])
            result.append(current)
        elif current and (option := re.match(r"\s*(-?\d+):\s*(.+)", line)):
            current["options"].append(dict(value=int(option[1]), label=option[2]))
    return result


def dhcp_server(options, addresses):
    match = re.search(r"dhcp_server_identifier\s*=\s*([0-9.]+)", options)
    if not match:
        raise RuntimeError("The camera USB network has no DHCP server address.")
    server = ipaddress.IPv4Address(match[1])
    for address in addresses:
        local = ipaddress.IPv4Interface(address)
        if server in local.network and server not in (local.ip, local.network.network_address, local.network.broadcast_address):
            return str(server)
    raise RuntimeError("The camera control address is outside its USB network.")


def control_endpoint(dev):
    # Inspect only the network interface belonging to the identified S3 USB
    # device. This avoids guessing a subnet or using the desktop's LAN DHCP.
    node = Path("/sys/class/video4linux") / Path(dev).name
    for usb in node.resolve().parents:
        if not (usb / "idVendor").exists():
            continue
        if (usb / "idVendor").read_text().strip() != "46f8" or (usb / "idProduct").read_text().strip() != "0855":
            break
        for interface in sorted(usb.glob("*/net/*")):
            options = command("nmcli", "-g", "DHCP4.OPTION", "device", "show", interface.name)
            links = json.loads(command("ip", "-j", "-4", "address", "show", "dev", interface.name))
            addresses = [f"{a['local']}/{a['prefixlen']}" for link in links for a in link.get("addr_info", []) if a.get("family") == "inet"]
            return dhcp_server(options, addresses)
        break
    raise RuntimeError("The S3 USB control network is not configured.")


DEPENDENCIES = {
    "exposure-ev": ("exposure-mode", 0),
    "exposure-iso": ("exposure-mode", 1),
    "exposure-time": ("exposure-mode", 1),
    "wb-temp": ("wb-mode", 0),
}


def related_controls(name, after=False):
    if name not in NAMES:
        raise ValueError("Unsupported setting.")
    names = {name}
    if name in DEPENDENCIES:
        names.add(DEPENDENCIES[name][0])
    if after:
        names.update(n for n, (mode, _) in DEPENDENCIES.items() if mode == name)
    return [n for n in NAMES if n in names]


def native_controls(client, names=None):
    from yolocam.params import PARAMS, sharpness_from_wire
    result = []
    for name in NAMES if names is None else names:
        label = NAMES[name]
        p = PARAMS[name]
        value = client.get_param_raw(name)
        available = value is not None
        if available and name == "sharpness":
            value = sharpness_from_wire(value)
        options = p.value_map or ({0: "Off", 1: "On"} if p.control_type == "toggle" else {})
        result.append(dict(id=name, label=label, kind="choice" if options else "slider",
                           minimum=p.min_val, maximum=p.max_val, step=1, value=value,
                           options=[dict(value=k, label=v) for k, v in options.items()], available=available, writable=available, requires=DEPENDENCIES.get(name)))
    values = {c["id"]: c["value"] for c in result}
    for c in result:
        if c["id"] in DEPENDENCIES:
            mode, required = DEPENDENCIES[c["id"]]
            c["writable"] = c["available"] and values.get(mode) == required
    return result


def validate(controls, name, value):
    c = next((c for c in controls if c["id"] == name), None)
    if not c or not c["writable"]:
        raise ValueError("This setting is unavailable in the current camera mode.")
    if not math.isfinite(value) or value != int(value):
        raise ValueError("Expected a whole-number setting.")
    value = int(value)
    if not c["minimum"] <= value <= c["maximum"]:
        raise ValueError("Setting is outside the supported range.")
    if c["options"] and value not in [o["value"] for o in c["options"]]:
        raise ValueError("Unsupported setting.")
    if (value - c["minimum"]) % c["step"]:
        raise ValueError("Unsupported setting step.")
    return value


def preview_modes(dev):
    """Expose only MJPEG combinations actually advertised by this camera."""
    modes = []
    pixel_format = None
    size = None
    for line in command("v4l2-ctl", "-d", dev, "--list-formats-ext").splitlines():
        if match := re.search(r"\[\d+\]: '([^']+)'", line):
            pixel_format, size = match[1], None
        elif match := re.search(r"Size: Discrete (\d+)x(\d+)", line):
            size = tuple(map(int, match.groups()))
        elif pixel_format == "MJPG" and size and (match := re.search(r"Interval: Discrete .*\(([\d.]+) fps\)", line)):
            fps = float(match[1])
            token = f"{size[0]}x{size[1]}@{fps:g}"
            if token not in [m["id"] for m in modes]:
                modes.append(dict(id=token, width=size[0], height=size[1], fps=fps))
    return modes


def preference_path():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "omarchy/yolocam-preview.json"


def preview_settings(dev):
    modes = preview_modes(dev)
    selected = "1280x720@30"
    try:
        selected = json.loads(preference_path().read_text())["mode"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if selected not in [m["id"] for m in modes]:
        selected = next((m["id"] for m in modes if m["id"] == "1280x720@30"), modes[0]["id"] if modes else "")
    return dict(previewModes=modes, previewMode=selected)


def save_preview_mode(dev, token):
    settings = preview_settings(dev)
    if token not in [m["id"] for m in settings["previewModes"]]:
        raise ValueError("This resolution and frame rate are not supported by the connected camera.")
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
        json.dump(dict(mode=token), temp)
    os.replace(temp.name, path)
    settings["previewMode"] = token
    return dict(ok=True, message="Preview format saved. Open a new preview to use it.", **settings)


def preview_command(dev, mode):
    return ["mpv", "--no-config", "--title=YoloCam S3 Preview", "--profile=low-latency",
            f"--demuxer-lavf-o=video_size={mode['width']}x{mode['height']},input_format=mjpeg,framerate={mode['fps']:g}",
            "av://v4l2:" + dev]


def operate(args):
    dev = device()
    if len(args) == 2 and args[0] == "preview-mode":
        return save_preview_mode(dev, args[1])
    if args == ["preview"]:
        settings = preview_settings(dev)
        mode = next((m for m in settings["previewModes"] if m["id"] == settings["previewMode"]), None)
        if mode is None:
            raise RuntimeError("No supported MJPEG preview formats are available.")
        # A regular temporary file avoids a full pipe blocking a detached mpv.
        # The descriptor remains valid in mpv after this helper closes its copy.
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(preview_command(dev, mode), stdout=log, stderr=log,
                                       start_new_session=True)
            try:
                code = process.wait(timeout=0.6)
            except subprocess.TimeoutExpired:
                pass
            else:
                if code:
                    log.seek(0)
                    detail = log.read()[-1200:].strip()
                    raise RuntimeError("Preview could not start. Close other camera apps and try again. " + detail)
        return dict(ok=True, message=f"Opening {mode['width']} × {mode['height']} at {mode['fps']:g} fps. Close the preview to release the camera.", **settings)
    if args != ["status"] and not (len(args) == 4 and args[0] == "set" and args[1] in ("usb", "full")):
        raise ValueError("Use status, preview, or set usb|full NAME VALUE.")
    writing = args[0] == "set"
    client = None
    full = False
    address = None
    control_error = ""
    # Do not silently change transports for writes: the displayed ranges differ.
    if not writing or args[1] == "full":
        try:
            from control_client import ControlClient
            address = control_endpoint(dev)
            client = ControlClient(ip=address, timeout=1.5)
            client.connect()
            controls = native_controls(client, related_controls(args[2]) if writing else None)
            if not any(c["available"] for c in controls):
                raise RuntimeError("Camera did not return any supported controls.")
            full = True
        except Exception as exc:
            control_error = str(exc)
            if client:
                client.disconnect()
                client = None
            if writing:
                raise RuntimeError("Full camera connection is unavailable. Refresh and try again.")
    try:
        if not full:
            controls = uvc_controls(dev)
        message = "" if full else "USB controls available. The separate connection for focus and exposure is not responding."
        if writing:
            name, value = args[2], validate(controls, args[2], float(args[3]))
            if full:
                from yolocam.params import sharpness_to_wire
                wire = sharpness_to_wire(value) if name == "sharpness" else value
                if not client.set_param(name, wire):
                    raise RuntimeError("The camera rejected this setting.")
                controls = native_controls(client, related_controls(name, after=True))
            else:
                command("v4l2-ctl", "-d", dev, "--set-ctrl", f"{name}={value}")
                controls = uvc_controls(dev)
            actual = next((c["value"] for c in controls if c["id"] == name), None)
            if actual != value:
                raise RuntimeError(f"Readback differs: requested {value}, camera returned {actual}. Refresh to read current settings.")
            message = "Setting applied and verified."
        if not controls:
            raise RuntimeError("The camera did not expose any supported settings.")
        unavailable = [c["label"] for c in controls if c.get("available") is False]
        if unavailable:
            message += (" " if message else "") + "Unavailable: " + ", ".join(unavailable) + ". Refresh to retry."
        return dict(ok=True, connected=True, transport="full" if full else "usb", device=dev,
                    controls=controls, partial=bool(writing and full), message=message, controlAddress=address,
                    controlError=control_error, **({} if writing and full else preview_settings(dev)))
    finally:
        if client:
            client.disconnect()


class OperationTimeout(BaseException):
    """Cannot be swallowed by fallback or library Exception handlers."""


def main():
    def timeout(*_):
        raise OperationTimeout("Camera request timed out. Reconnect USB and refresh.")
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(22)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    try:
        with (runtime / "omarchy-yolocam.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = operate(sys.argv[1:])
    except (Exception, OperationTimeout) as exc:
        result = dict(ok=False, connected=False, controls=[], message=str(exc) or "Camera is busy. Try again.")
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
