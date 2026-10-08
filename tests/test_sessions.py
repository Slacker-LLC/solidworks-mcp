# Copyright 2026 JIALE LIU
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from solidworks_mcp import sw_core as core, sw_manage as manage


class SessionContracts(unittest.TestCase):
    def test_rot_enumeration_filters_and_sorts_instance_monikers(self):
        names = ["SolidWorks_PID_42", "unrelated", "SolidWorks_PID_invalid", "SolidWorks_PID_7"]
        monikers = [SimpleNamespace(GetDisplayName=lambda *args, name=name: name) for name in names]
        with patch.object(core.pythoncom, "GetRunningObjectTable", return_value=monikers):
            self.assertEqual([pid for pid, _ in core.session_monikers()], [7, 42])

    def test_missing_selected_instance_never_falls_back_to_default(self):
        with patch.object(core, "_SESSION_PID", 7), patch.object(core, "session_monikers", return_value=[]), \
             patch.object(core.win32com.client, "GetActiveObject") as default:
            with self.assertRaisesRegex(RuntimeError, "no other session"):
                core.running_app()
            default.assert_not_called()

    def test_environment_and_explicit_default_precedence(self):
        with patch.dict(os.environ, {"SW_MCP_SESSION_PID": "42"}), patch.object(core, "_SESSION_PID", None):
            self.assertEqual(core.selected_session_pid(), 42)
            core._SESSION_PID = 0
            self.assertEqual(core.selected_session_pid(), 0)
        for raw in ("-1", "nan", "7.5"):
            with patch.dict(os.environ, {"SW_MCP_SESSION_PID": raw}), patch.object(core, "_SESSION_PID", None):
                with self.assertRaises(RuntimeError):
                    core.selected_session_pid()

    def test_selection_failure_preserves_previous_route(self):
        with patch.object(core, "_SESSION_PID", 7), patch.object(core, "session_app", side_effect=RuntimeError("unavailable")):
            with self.assertRaises(RuntimeError):
                manage.select_solidworks_session({"process_id": 42})
            self.assertEqual(core._SESSION_PID, 7)
        with patch.object(core, "_SESSION_PID", 7), patch.object(core, "session_app", return_value=SimpleNamespace(GetProcessID=lambda: 99)):
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                manage.select_solidworks_session({"process_id": 42})
            self.assertEqual(core._SESSION_PID, 7)

    def test_default_route_and_invalid_pid(self):
        with patch.object(core, "_SESSION_PID", 7), patch.object(core, "session_app", return_value=SimpleNamespace(GetProcessID=lambda: 42)):
            self.assertEqual(manage.select_solidworks_session({"process_id": 0})["data"]["attached_process_id"], 42)
            self.assertEqual(core._SESSION_PID, 0)
        for pid in (-1, True, 1.5, "42"):
            with patch.object(core, "session_app") as bind:
                with self.assertRaises(RuntimeError):
                    manage.select_solidworks_session({"process_id": pid})
                bind.assert_not_called()

    def test_unreadable_session_remains_visible(self):
        with patch.object(core, "selected_session_pid", return_value=7), \
             patch.object(manage, "running_app", side_effect=RuntimeError("unavailable")), \
             patch.object(core, "session_monikers", return_value=[(7, None)]), \
             patch.object(core, "session_app", side_effect=RuntimeError("native error")):
            data = manage.list_solidworks_sessions({})["data"]
            self.assertIsNone(data["attached_process_id"])
            self.assertFalse(data["sessions"][0]["readable"])
            self.assertEqual(data["sessions"][0]["process_id"], 7)
