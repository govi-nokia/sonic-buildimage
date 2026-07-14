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


class TestResolveLldpStatus(TestCase):
    def test_disabled(self):
        self.assertEqual(lldpmgrd.resolve_lldp_status("false"), "disabled")
        self.assertEqual(lldpmgrd.resolve_lldp_status("false", "TRANSMIT"), "disabled")

    def test_transmit_mode(self):
        self.assertEqual(lldpmgrd.resolve_lldp_status("true", "TRANSMIT"), "tx-only")

    def test_receive_mode(self):
        self.assertEqual(lldpmgrd.resolve_lldp_status("true", "RECEIVE"), "rx-only")

    def test_default_rx_and_tx(self):
        self.assertEqual(lldpmgrd.resolve_lldp_status("true"), "rx-and-tx")
        self.assertEqual(lldpmgrd.resolve_lldp_status("true", None), "rx-and-tx")


class TestBuildGlobalLldpCliCommands(TestCase):
    def test_defaults_only(self):
        cmds = lldpmgrd.build_global_lldp_cli_commands({})
        self.assertEqual(cmds, [
            ["lldpcli", "configure", "lldp", "tx-interval", "30"],
            ["lldpcli", "configure", "lldp", "tx-hold", "4"],
            ["lldpcli", "configure", "lldp", "status", "rx-and-tx"],
            ["lldpcli", "configure", "lldp", "management-addresses-advertisements"],
            ["lldpcli", "configure", "lldp", "capabilities-advertisements"],
        ])

    def test_full_config(self):
        cfg = {
            "enabled": "true",
            "mode": "TRANSMIT",
            "hello_time": "45",
            "multiplier": "5",
            "system_name": "my-switch",
            "system_description": "sonic-system",
            "supp_mgmt_address_tlv": "true",
            "supp_system_capabilities_tlv": "true",
        }
        cmds = lldpmgrd.build_global_lldp_cli_commands(cfg)
        self.assertEqual(cmds, [
            ["lldpcli", "configure", "lldp", "tx-interval", "45"],
            ["lldpcli", "configure", "lldp", "tx-hold", "5"],
            ["lldpcli", "configure", "lldp", "status", "tx-only"],
            ["lldpcli", "configure", "system", "hostname", "my-switch"],
            ["lldpcli", "configure", "system", "description", "sonic-system"],
            ["lldpcli", "unconfigure", "lldp", "management-addresses-advertisements"],
            ["lldpcli", "unconfigure", "lldp", "capabilities-advertisements"],
        ])

    def test_receive_mode(self):
        cmds = lldpmgrd.build_global_lldp_cli_commands({"enabled": "true", "mode": "RECEIVE"})
        status_cmd = [c for c in cmds if len(c) >= 5 and c[3] == "status"][0]
        self.assertEqual(status_cmd, ["lldpcli", "configure", "lldp", "status", "rx-only"])

    def test_disabled_ignores_mode(self):
        cmds = lldpmgrd.build_global_lldp_cli_commands({"enabled": "false", "mode": "TRANSMIT"})
        status_cmd = [c for c in cmds if len(c) >= 5 and c[3] == "status"][0]
        self.assertEqual(status_cmd, ["lldpcli", "configure", "lldp", "status", "disabled"])

    def test_suppress_tlv_false_uses_configure(self):
        cmds = lldpmgrd.build_global_lldp_cli_commands({
            "supp_mgmt_address_tlv": "false",
            "supp_system_capabilities_tlv": "false",
        })
        self.assertIn(
            ["lldpcli", "configure", "lldp", "management-addresses-advertisements"],
            cmds,
        )
        self.assertIn(
            ["lldpcli", "configure", "lldp", "capabilities-advertisements"],
            cmds,
        )

    def test_optional_system_fields_omitted_when_absent(self):
        cmds = lldpmgrd.build_global_lldp_cli_commands({"hello_time": "12"})
        for cmd in cmds:
            self.assertNotEqual(cmd[2:4], ["system", "hostname"])
            self.assertNotEqual(cmd[2:4], ["system", "description"])


class TestApplyGlobalLldpConfig(TestCase):
    def _make_mgr(self):
        return lldpmgrd.LldpManager("test")

    def test_runs_all_commands(self):
        mgr = self._make_mgr()
        cfg = {"hello_time": "20", "multiplier": "3"}
        expected_cmds = lldpmgrd.build_global_lldp_cli_commands(cfg)

        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(0, "")) as run_cmd:
            with mock.patch.object(mgr, "apply_management_pattern"):
                mgr.apply_global_lldp_config(cfg)

        self.assertEqual(run_cmd.call_count, len(expected_cmds))
        actual_cmds = [call.args[1] for call in run_cmd.call_args_list]
        self.assertEqual(actual_cmds, expected_cmds)

    def test_logs_warning_on_command_failure(self):
        mgr = self._make_mgr()
        cfg = {"hello_time": "20"}

        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(1, "error")):
            with mock.patch.object(mgr, "log_warning") as log_warning:
                with mock.patch.object(mgr, "apply_management_pattern"):
                    mgr.apply_global_lldp_config(cfg)

        self.assertTrue(log_warning.called)

    def test_applies_management_pattern_after_global_commands(self):
        mgr = self._make_mgr()
        with mock.patch.object(lldpmgrd, "run_cmd", return_value=(0, "")):
            with mock.patch.object(mgr, "apply_management_pattern") as apply_pat:
                mgr.apply_global_lldp_config({"hello_time": "20"})
        apply_pat.assert_called_once()


class TestLldpProcessLldpGlobalEvent(TestCase):
    def _make_mgr(self):
        return lldpmgrd.LldpManager("test")

    def test_set_applies_config(self):
        mgr = self._make_mgr()
        fvp = [("hello_time", "60"), ("enabled", "true")]

        with mock.patch.object(mgr, "apply_global_lldp_config") as apply_cfg:
            mgr.lldp_process_lldp_global_event("SET", fvp)

        apply_cfg.assert_called_once_with({"hello_time": "60", "enabled": "true"})

    def test_del_applies_defaults(self):
        mgr = self._make_mgr()

        with mock.patch.object(mgr, "apply_global_lldp_config") as apply_cfg:
            mgr.lldp_process_lldp_global_event("DEL", None)

        apply_cfg.assert_called_once_with({})

    def test_ignores_unknown_opcode(self):
        mgr = self._make_mgr()

        with mock.patch.object(mgr, "apply_global_lldp_config") as apply_cfg:
            mgr.lldp_process_lldp_global_event("GET", [])

        apply_cfg.assert_not_called()


class TestCustomTlvCliCommands(TestCase):
    def test_hex_bytes_for_lldpcli(self):
        self.assertEqual(lldpmgrd.hex_bytes_for_lldpcli("00:90:69"), "00,90,69")
        self.assertEqual(lldpmgrd.hex_bytes_for_lldpcli("009069"), "00,90,69")
        self.assertEqual(lldpmgrd.hex_bytes_for_lldpcli(""), None)
        self.assertEqual(lldpmgrd.hex_bytes_for_lldpcli("zzz"), None)

    def test_rebuild_clears_then_adds(self):
        cmds = lldpmgrd.build_custom_tlv_cli_commands([
            {"oui": "00:90:69", "oui_subtype": "1", "value": "aabb"},
            {"oui": "00,11,22", "oui_subtype": "2"},
        ])
        self.assertEqual(cmds[0], ["lldpcli", "unconfigure", "lldp", "custom-tlv"])
        self.assertEqual(cmds[1], [
            "lldpcli", "configure", "lldp", "custom-tlv", "add",
            "oui", "00,90,69", "subtype", "1", "oui-info", "aa,bb",
        ])
        self.assertEqual(cmds[2], [
            "lldpcli", "configure", "lldp", "custom-tlv", "add",
            "oui", "00,11,22", "subtype", "2",
        ])

    def test_skips_incomplete_entries(self):
        cmds = lldpmgrd.build_custom_tlv_cli_commands([{"oui": "00:90:69"}])
        self.assertEqual(cmds, [["lldpcli", "unconfigure", "lldp", "custom-tlv"]])


class TestApplyManagementPattern(TestCase):
    def _make_mgr(self):
        return lldpmgrd.LldpManager("test")

    def test_uses_configured_interface(self):
        mgr = self._make_mgr()
        mgr.lldp_global_cfg = {"management_interface": "eth0"}
        with mock.patch.object(mgr, "update_mgmt_addr") as update:
            with mock.patch.object(mgr, "lldp_get_mgmt_ip") as get_ip:
                mgr.apply_management_pattern()
        update.assert_called_once_with("eth0")
        get_ip.assert_not_called()

    def test_falls_back_to_mgmt_ip(self):
        mgr = self._make_mgr()
        mgr.lldp_global_cfg = {}
        with mock.patch.object(mgr, "lldp_get_mgmt_ip", return_value="10.0.0.1"):
            with mock.patch.object(mgr, "update_mgmt_addr") as update:
                mgr.apply_management_pattern()
        update.assert_called_once_with("10.0.0.1")

    def test_mgmt_ip_change_ignored_when_interface_configured(self):
        mgr = self._make_mgr()
        mgr.lldp_global_cfg = {"management_interface": "eth0"}
        with mock.patch.object(mgr, "update_mgmt_addr") as update:
            mgr.lldp_process_mgmt_info_change("SET", {}, "eth0|10.0.0.1/24")
        update.assert_not_called()
