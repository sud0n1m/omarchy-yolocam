# Camera USB network setup

The S3 exposes a UVC video interface and a separate USB Ethernet interface for
native settings such as focus and exposure. Working video does not imply that
the control network is configured.

The camera supplies DHCP. Different units or firmware can use different
subnets, so do not copy a fixed address from another setup. This plugin locates
the Ethernet interface belonging to the identified S3, reads its DHCP server
identifier, checks that the address is in the USB interface's subnet, and
connects to TCP port 12345 there.

## Identify the camera interface

```bash
nmcli device status
nmcli device show CAMERA_INTERFACE
```

Replace `CAMERA_INTERFACE` with the USB Ethernet device from the first command.
Confirm that `GENERAL.VENDOR` is YoloCam and `GENERAL.PRODUCT` is S3. Note its
`GENERAL.CONNECTION` profile name and `GENERAL.HWADDR` MAC address.
Apply the following commands only to the dedicated camera profile, never
your normal Ethernet or Wi-Fi connection.

## Existing camera profile

Replace `CAMERA_PROFILE` with that interface's dedicated profile name:

```bash
nmcli connection modify CAMERA_PROFILE \
  ipv4.method auto ipv4.addresses '' \
  ipv4.never-default yes ipv4.ignore-auto-dns yes
nmcli -w 20 connection up CAMERA_PROFILE
```

This replaces a stale static address with the camera's own DHCP lease and
prevents the camera from supplying your desktop's default route or DNS.
It does not reset the USB video interface or update camera firmware.

## New dedicated profile

If no camera profile exists, replace `CAMERA_MAC` with the camera interface's
MAC address. The MAC binding prevents the profile from matching another NIC:

```bash
nmcli connection add type ethernet con-name yolocam-s3 ifname '*' \
  802-3-ethernet.mac-address CAMERA_MAC \
  ipv4.method auto ipv4.never-default yes ipv4.ignore-auto-dns yes \
  ipv6.method disabled
nmcli -w 20 connection up yolocam-s3
```

## Verify

```bash
nmcli -f GENERAL,IP4,DHCP4 device show CAMERA_INTERFACE
ip route show default
omarchy-shell yolocam refresh
omarchy-shell yolocam state
```

The USB interface should have a DHCP lease and a `dhcp_server_identifier`.
The panel should show **Full Camera Controls**. Its diagnostic state reports
`transport: full` and the discovered `controlAddress`. Your default route
should still use your normal network connection.

If unavailable, the panel keeps standard UVC controls. Close other control
software, reconnect USB if necessary, and refresh. `controlError` in the
diagnostic state can distinguish missing DHCP configuration from protocol
read errors. The plugin does not rewrite network settings on its own.
