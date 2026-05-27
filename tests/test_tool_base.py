"""MockTool base — subclass-init enforcement (§1.4.7 / §G.3)."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok


class _DummyArgs(BaseModel):
    x: int = 1


def test_subclass_missing_intended_entities_fails():
    with pytest.raises(TypeError, match="intended_entities"):

        class Broken(MockTool):
            name = "broken"
            domain = "manage_alarms"
            action = "broken"
            args_model = _DummyArgs

            @staticmethod
            def referenced_entities(args: BaseModel) -> list[str]:
                return []

            def run(self, args, world):
                return ok()


def test_subclass_missing_referenced_entities_fails():
    with pytest.raises(TypeError, match="referenced_entities"):

        class Broken2(MockTool):
            name = "broken2"
            domain = "manage_alarms"
            action = "broken2"
            args_model = _DummyArgs

            @staticmethod
            def intended_entities(args: BaseModel) -> list[str]:
                return []

            def run(self, args, world):
                return ok()


def test_subclass_non_static_method_fails():
    with pytest.raises(TypeError, match="staticmethod"):

        class Broken3(MockTool):
            name = "broken3"
            domain = "manage_alarms"
            action = "broken3"
            args_model = _DummyArgs

            def intended_entities(self, args):  # noqa: D401 — intentional bad signature
                return []

            @staticmethod
            def referenced_entities(args):
                return []

            def run(self, args, world):
                return ok()


def test_subclass_missing_name_fails():
    with pytest.raises(TypeError, match="`name`"):

        class Broken4(MockTool):
            domain = "manage_alarms"
            action = "broken4"
            args_model = _DummyArgs

            @staticmethod
            def intended_entities(args):
                return []

            @staticmethod
            def referenced_entities(args):
                return []

            def run(self, args, world):
                return ok()


def test_good_subclass_registers_cleanly():
    class Good(MockTool):
        name = "good"
        domain = "manage_alarms"
        action = "good"
        args_model = _DummyArgs

        @staticmethod
        def intended_entities(args):
            return ["alarms.x"]

        @staticmethod
        def referenced_entities(args):
            return []

        def run(self, args, world):
            return ok({"x": args.x})

    g = Good()
    res = g.run(_DummyArgs(x=42), None)
    assert res.ok and res.data["x"] == 42


def test_fail_helper():
    r = fail(ErrorCode.POINT_NOT_FOUND, "no such point")
    assert not r.ok and r.error_code == ErrorCode.POINT_NOT_FOUND
    assert r.error_msg == "no such point"


def test_ok_helper():
    r = ok({"a": 1}, world_diff={"added_or_modified": {"k": 1}, "removed": []})
    assert r.ok and r.error_code == ErrorCode.OK
    assert r.world_diff and "added_or_modified" in r.world_diff
