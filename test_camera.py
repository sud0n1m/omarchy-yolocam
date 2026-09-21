import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import camera


class CameraTests(unittest.TestCase):
    def controls(self):
        text = '''
User Controls
 white_balance_automatic 0x0098090c (bool) : default=1 value=1
 white_balance_temperature 0x0098091a (int) : min=2000 max=10000 step=1 value=5500 flags=inactive, has-min-max
 power_line_frequency 0x00980918 (menu) : min=0 max=2 default=0 value=2 (60 Hz)
     0: Disabled
     1: 50 Hz
     2: 60 Hz
 zoom_absolute 0x009a090d (int) : min=50 max=98 step=1 default=50 value=50
'''
        with patch.object(camera, "command", return_value=text):
            return camera.uvc_controls("/dev/test")

    def test_usb_menu_and_inactive_temperature(self):
        controls = self.controls()
        self.assertFalse(controls[1]["writable"])
        self.assertEqual(controls[2]["options"][-1], dict(value=2, label="60 Hz"))
        self.assertEqual(camera.validate(controls, "zoom_absolute", 51), 51)

    def test_reject_unavailable_out_of_range_fractional_and_unknown(self):
        for name, value in [("white_balance_temperature", 5500), ("zoom_absolute", 99),
                            ("zoom_absolute", 51.5), ("zoom_absolute", float("nan")),
                            ("factory-reset", 1)]:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                camera.validate(self.controls(), name, value)

    def test_failed_readback_is_not_success(self):
        with patch.object(camera, "device", return_value="/dev/test"), \
             patch.object(camera, "uvc_controls", side_effect=[self.controls(), self.controls()]), \
             patch.object(camera, "command"), self.assertRaisesRegex(RuntimeError, "Readback differs"):
            camera.operate(["set", "usb", "zoom_absolute", "51"])

    def test_verified_usb_write(self):
        changed = self.controls()
        changed[-1]["value"] = 51
        with patch.object(camera, "device", return_value="/dev/test"), \
             patch.object(camera, "uvc_controls", side_effect=[self.controls(), changed]), \
             patch.object(camera, "preview_settings", return_value={}), \
             patch.object(camera, "command") as call:
            result = camera.operate(["set", "usb", "zoom_absolute", "51"])
        self.assertTrue(result["ok"])
        call.assert_called_once_with("v4l2-ctl", "-d", "/dev/test", "--set-ctrl", "zoom_absolute=51")

    def modes(self):
        text = """
 [0]: 'MJPG' (Motion-JPEG, compressed)
  Size: Discrete 1280x720
   Interval: Discrete 0.017s (60.000 fps)
   Interval: Discrete 0.033s (30.000 fps)
  Size: Discrete 3840x2160
   Interval: Discrete 0.033s (30.000 fps)
   Interval: Discrete 0.040s (25.000 fps)
 [1]: 'NV12' (Y/UV 4:2:0)
  Size: Discrete 1920x1080
   Interval: Discrete 0.017s (60.000 fps)
"""
        with patch.object(camera, "command", return_value=text):
            return camera.preview_modes("/dev/test")

    def test_modes_keep_only_advertised_mjpeg_combinations(self):
        self.assertEqual([m["id"] for m in self.modes()],
                         ["1280x720@60", "1280x720@30", "3840x2160@30", "3840x2160@25"])

    def test_preference_persistence_and_reject_unsupported_4k60(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(camera, "preference_path", return_value=Path(folder) / "preview.json"), \
             patch.object(camera, "preview_modes", return_value=self.modes()):
            camera.save_preview_mode("/dev/test", "3840x2160@30")
            self.assertEqual(camera.preview_settings("/dev/test")["previewMode"], "3840x2160@30")
            with self.assertRaises(ValueError):
                camera.save_preview_mode("/dev/test", "3840x2160@60")
            self.assertEqual(camera.preview_settings("/dev/test")["previewMode"], "3840x2160@30")
            with patch.object(camera, "preview_modes", return_value=self.modes()[:2]):
                self.assertEqual(camera.preview_settings("/dev/test")["previewMode"], "1280x720@30")

    def test_preview_launches_selected_format(self):
        mode = self.modes()[2]
        with patch.object(camera, "device", return_value="/dev/test"), \
             patch.object(camera, "preview_settings", return_value=dict(previewModes=[mode], previewMode=mode["id"])), \
             patch.object(camera.subprocess, "Popen") as launch:
            self.assertTrue(camera.operate(["preview"])["ok"])
        self.assertIn("--demuxer-lavf-o=video_size=3840x2160,input_format=mjpeg,framerate=30", launch.call_args.args[0])

    def test_camera_dhcp_address_is_discovered_not_hardcoded(self):
        self.assertEqual(camera.dhcp_server("dhcp_server_identifier = 192.168.124.10", ["192.168.124.11/24"]), "192.168.124.10")
        self.assertEqual(camera.dhcp_server("dhcp_server_identifier = 192.168.126.10", ["192.168.126.11/24"]), "192.168.126.10")

    def test_control_address_must_belong_to_camera_usb_network(self):
        for options in ("", "dhcp_server_identifier = 10.0.0.1", "dhcp_server_identifier = 192.168.124.11", "dhcp_server_identifier = 192.168.124.255"):
            with self.subTest(options=options), self.assertRaises(RuntimeError):
                camera.dhcp_server(options, ["192.168.124.11/24"])

    def test_unavailable_control_network_preserves_usb_fallback_and_preview(self):
        controls = self.controls()
        with patch.object(camera, "device", return_value="/dev/test"), \
             patch.object(camera, "control_endpoint", side_effect=RuntimeError("No camera DHCP lease")), \
             patch.object(camera, "uvc_controls", return_value=controls), \
             patch.object(camera, "preview_settings", return_value=dict(previewMode="1280x720@30", previewModes=self.modes())):
            result = camera.operate(["status"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["transport"], "usb")
        self.assertEqual(result["controls"], controls)
        self.assertEqual(result["previewMode"], "1280x720@30")


if __name__ == "__main__":
    unittest.main()
