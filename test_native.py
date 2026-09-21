import socket
import struct
import unittest
from unittest.mock import patch

import camera  # Adds the unchanged upstream schema/params to sys.path.
from control_client import ControlClient
from yolocam import yolocam_pb2 as pb
from yolocam.protocol import pack_frame


class NativeTests(unittest.TestCase):
    def client(self):
        class FakeCamera:
            def __init__(self):
                self.values = dict.fromkeys(camera.NAMES, 0)
                self.values.update({'zoom': 50, 'focus-mode': 3, 'exposure-ev': 8,
                                    'exposure-iso': 500, 'exposure-time': 16666, 'wb-mode': 1})
                self.calls = []
                self.ignore_write = False
            def connect(self): pass
            def disconnect(self): pass
            def get_param_raw(self, name):
                self.calls.append(('get', name))
                return self.values[name]
            def set_param(self, name, raw):
                self.calls.append(('set', name))
                if not self.ignore_write:
                    self.values[name] = raw
                return True
        return FakeCamera()

    def operate(self, client, args):
        with patch.object(camera, 'device', return_value='/dev/test'), \
             patch.object(camera, 'control_endpoint', return_value='192.0.2.1'), \
             patch('control_client.ControlClient', return_value=client), \
             patch.object(camera, 'preview_settings', return_value={'previewModes': []}) as preview:
            result = camera.operate(args)
            if args[0] == 'set':
                preview.assert_not_called()
            return result

    def test_focus_write_three_requests_and_verified_delta(self):
        client = self.client()
        result = self.operate(client, ['set', 'full', 'focus-mode', '4'])
        self.assertEqual(client.calls, [('get', 'focus-mode'), ('set', 'focus-mode'), ('get', 'focus-mode')])
        self.assertTrue(result['partial'])
        self.assertEqual(result['controls'][0]['value'], 4)

    def test_ev_write_five_requests_and_fresh_mode_check(self):
        client = self.client()
        result = self.operate(client, ['set', 'full', 'exposure-ev', '9'])
        self.assertEqual(len(client.calls), 5)
        self.assertEqual([c['id'] for c in result['controls']], ['exposure-mode', 'exposure-ev'])
        client.values['exposure-mode'] = 1
        client.calls.clear()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.operate(client, ['set', 'full', 'exposure-ev', '9'])
        self.assertTrue(all(op == 'get' for op, name in client.calls))

    def test_mode_change_refreshes_dependencies_and_gating(self):
        client = self.client()
        result = self.operate(client, ['set', 'full', 'exposure-mode', '1'])
        self.assertEqual(len(client.calls), 6)
        controls = {c['id']: c for c in result['controls']}
        self.assertTrue(controls['exposure-iso']['writable'])
        self.assertTrue(controls['exposure-time']['writable'])
        self.assertFalse(controls['exposure-ev']['writable'])
        result = self.operate(client, ['set', 'full', 'wb-mode', '0'])
        self.assertTrue(next(c for c in result['controls'] if c['id'] == 'wb-temp')['writable'])

    def test_unsupported_control_preserves_other_controls(self):
        client = self.client()
        client.values['hdr'] = None
        result = self.operate(client, ['status'])
        self.assertEqual(result['transport'], 'full')
        self.assertEqual(len(result['controls']), 15)
        self.assertFalse(result['controls'][-1]['writable'])
        self.assertIn('HDR', result['message'])

    def test_missing_mode_disables_dependents(self):
        client = self.client()
        client.values['exposure-mode'] = None
        controls = camera.native_controls(client)
        self.assertFalse(any(c['writable'] for c in controls if c['id'].startswith('exposure-')))

    def test_mismatch_and_missing_readback_fail(self):
        client = self.client()
        client.ignore_write = True
        with self.assertRaisesRegex(RuntimeError, 'Readback differs'):
            self.operate(client, ['set', 'full', 'focus-mode', '4'])
        with patch.object(client, 'get_param_raw', side_effect=[3, None]), self.assertRaisesRegex(RuntimeError, 'Readback differs'):
            self.operate(client, ['set', 'full', 'focus-mode', '4'])

    def test_operation_deadline_is_not_swallowed_by_usb_fallback(self):
        client = self.client()
        with patch.object(client, 'get_param_raw', side_effect=camera.OperationTimeout('deadline')), \
             patch.object(camera, 'uvc_controls') as usb, self.assertRaises(camera.OperationTimeout):
            self.operate(client, ['status'])
        usb.assert_not_called()

    def test_unknown_command_cannot_be_sent(self):
        client = self.client()
        with self.assertRaises(RuntimeError):
            self.operate(client, ['set', 'full', 'factory-reset', '1'])
        self.assertEqual(client.calls, [])

    def test_zoom_and_sharpness_wire_conversion(self):
        client = self.client()
        self.operate(client, ['set', 'full', 'zoom', '66'])
        self.assertEqual(client.values['zoom'], 66)
        self.operate(client, ['set', 'full', 'sharpness', '-5'])
        from yolocam.params import sharpness_to_wire
        self.assertEqual(client.values['sharpness'], sharpness_to_wire(-5))

    def test_preview_startup_failure_is_reported(self):
        mode = dict(id='1280x720@30', width=1280, height=720, fps=30)
        with patch.object(camera, 'device', return_value='/dev/test'), \
             patch.object(camera, 'preview_settings', return_value=dict(previewModes=[mode], previewMode=mode['id'])), \
             patch.object(camera.subprocess, 'Popen') as launch:
            launch.return_value.wait.return_value = 2
            with self.assertRaisesRegex(RuntimeError, 'Preview could not start'):
                camera.operate(['preview'])


class FakeSocket:
    def __init__(self, data, fragment=7):
        self.data = data
        self.fragment = fragment
        self.closed = False
        self.timeouts = []
        self.sent = []
    def recv(self, size):
        chunk, self.data = self.data[:min(size, self.fragment)], self.data[min(size, self.fragment):]
        return chunk
    def sendall(self, data): self.sent.append(data)
    def settimeout(self, timeout): self.timeouts.append(timeout)
    def close(self): self.closed = True


def response(seq=1, cid=pb.CID_FOCUS_MODE, kind=pb.TYPE_RESPONSE, code=200, value=3):
    msg = pb.Message(type=kind, seq=seq, id=cid, resp_code=code)
    msg.body.int_val = value
    return pack_frame(msg.SerializeToString())


class TransportTests(unittest.TestCase):
    def client(self, data, fragment=7):
        client = ControlClient('192.0.2.1')
        sock = FakeSocket(data, fragment)
        client.sock = sock
        return client, sock

    def test_fragmented_reply_and_notifications(self):
        client, sock = self.client(response(kind=pb.TYPE_NOTIFY) + response(), 1)
        self.assertEqual(client.get_param_raw('focus-mode'), 3)
        self.assertEqual(len(sock.sent), 1)

    def test_wrong_id_sequence_and_type_do_not_satisfy_request(self):
        client, _ = self.client(response(cid=pb.CID_ZOOM) + response(seq=9) + response(kind=pb.TYPE_SET) + response(value=4))
        self.assertEqual(client.get_param_raw('focus-mode'), 4)

    def test_error_response_body_is_not_a_value(self):
        client, _ = self.client(response(code=500))
        self.assertIsNone(client.get_param_raw('focus-mode'))
        client, _ = self.client(response(code=500))
        self.assertFalse(client.set_param('focus-mode', 4))

    def test_missing_body_is_unavailable(self):
        msg = pb.Message(type=pb.TYPE_RESPONSE, seq=1, id=pb.CID_FOCUS_MODE, resp_code=200)
        client, _ = self.client(pack_frame(msg.SerializeToString()))
        self.assertIsNone(client.get_param_raw('focus-mode'))

    def test_truncated_bad_length_and_bad_checksum_close_connection(self):
        malformed = bytearray(response())
        malformed[9:13] = struct.pack('>I', 99999)
        checksum = bytearray(response())
        checksum[-2] ^= 1
        for data in (response()[:-2], bytes(malformed), bytes(checksum), b'\x08\x00\x00\x00' + struct.pack('<I', 2**30)):
            with self.subTest(data=data):
                client, sock = self.client(data)
                with self.assertRaises((ValueError, ConnectionError)):
                    client.get_param_raw('focus-mode')
                self.assertTrue(sock.closed)
                self.assertIsNone(client.sock)

    def test_whole_reply_deadline_stops_slow_fragment_stream(self):
        client, sock = self.client(response(), 1)
        clock = iter(i * 0.1 for i in range(100))
        with patch('control_client.time.monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                client.get_param_raw('focus-mode')
        self.assertTrue(sock.closed)
        self.assertTrue(all(t <= 1.5 for t in sock.timeouts))

    def test_notification_limit(self):
        client, sock = self.client(response(kind=pb.TYPE_NOTIFY) * 51, 1000)
        with self.assertRaisesRegex(RuntimeError, 'matching response'):
            client.get_param_raw('focus-mode')
        self.assertTrue(sock.closed)

    def test_request_pacing_preserved(self):
        client, _ = self.client(response() + response(seq=2))
        with patch('control_client.time.sleep') as sleep, patch('control_client.time.monotonic', return_value=10):
            client.get_param_raw('focus-mode')
            client.get_param_raw('focus-mode')
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)

    def test_socket_timeout_closes_connection(self):
        client, sock = self.client(b'')
        with patch.object(sock, 'recv', side_effect=socket.timeout), self.assertRaises(TimeoutError):
            client.get_param_raw('focus-mode')
        self.assertTrue(sock.closed)


if __name__ == '__main__':
    unittest.main()
