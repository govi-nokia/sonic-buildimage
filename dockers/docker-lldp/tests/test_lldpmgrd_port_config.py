import importlib.machinery
import importlib.util
import os
import sys
from unittest import TestCase, mock

# Mock SONiC dependencies before loading lldpmgrd.
class _DaemonBase:
    def __init__(self, log_identifier):
        self.log_identifier = log_identifier

    def log_debug(self, msg):
        pass

    def log_info(self, msg):
        pass

    def log_warning(self, msg):
        pass

    def log_error(self, msg):
        pass

    def set_min_log_priority_info(self):
        pass


_daemon_base_mod = mock.MagicMock()
_daemon_base_mod.DaemonBase = _DaemonBase

_sonic_py_common = mock.MagicMock()
_sonic_py_common.daemon_base = _daemon_base_mod
_sonic_py_common.interface.inband_prefix.return_value = "Ethernet-BP"
_sonic_py_common.interface.recirc_prefix.return_value = "Ethernet-Rec"
_sonic_py_common.interface.backplane_prefix.return_value = "Ethernet-BP"
_sonic_py_common.device_info.is_frontend_port_present_in_host.return_value = False

_swsscommon = mock.MagicMock()
_swsscommon.DBConnector.return_value = mock.MagicMock()
_swsscommon.Table.return_value = mock.MagicMock()
_swsscommon.SubscriberStateTable.return_value = mock.MagicMock()
_swsscommon.Select.return_value = mock.MagicMock()
_swsscommon.CFG_DEVICE_METADATA_TABLE_NAME = "DEVICE_METADATA"
_swsscommon.CFG_PORT_TABLE_NAME = "PORT"
_swsscommon.CFG_MGMT_INTERFACE_TABLE_NAME = "MGMT_INTERFACE"
_swsscommon.APP_PORT_TABLE_NAME = "PORT_TABLE"
_swsscommon.STATE_PORT_TABLE_NAME = "PORT_TABLE"

sys.modules["sonic_py_common"] = _sonic_py_common
sys.modules["sonic_py_common.daemon_base"] = _daemon_base_mod
sys.modules["sonic_py_common.interface"] = _sonic_py_common.interface
sys.modules["sonic_py_common.device_info"] = _sonic_py_common.device_info
sys.modules["swsscommon"] = _swsscommon
sys.modules["swsscommon.swsscommon"] = _swsscommon

_LLDPMGRD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lldpmgrd")
_LOADER = importlib.machinery.SourceFileLoader("lldpmgrd", _LLDPMGRD_PATH)
_SPEC = importlib.util.spec_from_loader("lldpmgrd", _LOADER)
lldpmgrd = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(lldpmgrd)


class TestBuildPortLldpStatusCommand(TestCase):
    def test_enabled_default_rx_and_tx(self):
        cmd = lldpmgrd.build_port_lldp_status_command("Ethernet0", {"enabled": "true"})
        self.assertEqual(
            cmd,
            ["lldpcli", "configure", "ports", "Ethernet0", "lldp", "status", "rx-and-tx"],
        )

    def test_disabled(self):
        cmd = lldpmgrd.build_port_lldp_status_command("Ethernet0", {"enabled": "false"})
        self.assertEqual(
            cmd,
            ["lldpcli", "configure", "ports", "Ethernet0", "lldp", "status", "disabled"],
        )

    def test_transmit_mode(self):
        cmd = lldpmgrd.build_port_lldp_status_command(
            "Ethernet4", {"enabled": "true", "mode": "TRANSMIT"}
        )
        self.assertEqual(
            cmd,
            ["lldpcli", "configure", "ports", "Ethernet4", "lldp", "status", "tx-only"],
        )

    def test_receive_mode(self):
        cmd = lldpmgrd.build_port_lldp_status_command(
            "Ethernet8", {"enabled": "true", "mode": "RECEIVE"}
        )
        self.assertEqual(
            cmd,
            ["lldpcli", "configure", "ports", "Ethernet8", "lldp", "status", "rx-only"],
        )

    def test_revert_to_global_on_delete(self):
        global_cfg = {"enabled": "false", "mode": "TRANSMIT"}
        cmd = lldpmgrd.build_port_lldp_status_command("Ethernet0", global_cfg)
        self.assertEqual(
            cmd,
            ["lldpcli", "configure", "ports", "Ethernet0", "lldp", "status", "disabled"],
        )


class TestIsLldpSkippedPort(TestCase):
    def test_skips_special_ports(self):
        self.assertTrue(lldpmgrd.is_lldp_skipped_port("Ethernet-BP0"))
        self.assertTrue(lldpmgrd.is_lldp_skipped_port("Ethernet-Rec0"))

    def test_allows_frontend_ports(self):
        self.assertFalse(lldpmgrd.is_lldp_skipped_port("Ethernet0"))


class TestApplyPortLldpStatus(TestCase):
    def _make_mgr(self):
        return lldpmgrd.LldpManager("test")

    def test_runs_status_command(self):
        mgr = self._make_mgr()
        cfg = {"enabled": "true", "mode": "TRANSMIT"}
        expected = lldpmgrd.build_port_lldp_status_command("Ethernet0", cfg)

        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(0, "")) as run_cmd:
            mgr.apply_port_lldp_status("Ethernet0", cfg)

        run_cmd.assert_called_once_with(mgr, expected)

    def test_skips_special_ports(self):
        mgr = self._make_mgr()

        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(0, "")) as run_cmd:
            mgr.apply_port_lldp_status("Ethernet-BP0", {"enabled": "true"})

        run_cmd.assert_not_called()

    def test_logs_warning_on_failure(self):
        mgr = self._make_mgr()

        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(1, "error")):
            with mock.patch.object(mgr, "log_warning") as log_warning:
                mgr.apply_port_lldp_status("Ethernet0", {"enabled": "true"})

        self.assertTrue(log_warning.called)


class TestApplyAllPortLldpStatus(TestCase):
    def _make_mgr(self):
        return lldpmgrd.LldpManager("test")

    def test_applies_all_configured_ports(self):
        mgr = self._make_mgr()
        mgr.lldp_port_table.getKeys.return_value = ["Ethernet0", "Ethernet4"]
        mgr.lldp_port_table.get.side_effect = [
            (True, [("enabled", "true"), ("mode", "TRANSMIT")]),
            (True, [("enabled", "false")]),
        ]

        with mock.patch.object(mgr, "apply_port_lldp_status") as apply_status:
            mgr.apply_all_port_lldp_status()

        apply_status.assert_any_call("Ethernet0", {"enabled": "true", "mode": "TRANSMIT"})
        apply_status.assert_any_call("Ethernet4", {"enabled": "false"})
        self.assertEqual(apply_status.call_count, 2)


class TestLldpProcessLldpPortEvent(TestCase):
    def _make_mgr(self):
        mgr = lldpmgrd.LldpManager("test")
        mgr.lldp_global_cfg = {"enabled": "true", "mode": "RECEIVE"}
        return mgr

    def test_set_applies_port_config(self):
        mgr = self._make_mgr()
        fvp = [("enabled", "true"), ("mode", "TRANSMIT")]

        with mock.patch.object(mgr, "apply_port_lldp_status") as apply_status:
            mgr.lldp_process_lldp_port_event("SET", "Ethernet0", fvp)

        apply_status.assert_called_once_with(
            "Ethernet0", {"enabled": "true", "mode": "TRANSMIT"}
        )

    def test_del_reverts_to_global_config(self):
        mgr = self._make_mgr()

        with mock.patch.object(mgr, "apply_port_lldp_status") as apply_status:
            mgr.lldp_process_lldp_port_event("DEL", "Ethernet0", None)

        apply_status.assert_called_once_with(
            "Ethernet0", {"enabled": "true", "mode": "RECEIVE"}
        )

    def test_ignores_unknown_opcode(self):
        mgr = self._make_mgr()

        with mock.patch.object(mgr, "apply_port_lldp_status") as apply_status:
            mgr.lldp_process_lldp_port_event("GET", "Ethernet0", [])

        apply_status.assert_not_called()
