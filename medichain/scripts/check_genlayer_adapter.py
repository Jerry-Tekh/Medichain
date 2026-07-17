#!/usr/bin/env python3
"""No-dependency Bradbury readiness checks for the GenLayer adapter."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "contract" / "genlayer_adapter.py"
PINNED_RUNNER = (
    '# { "Depends": '
    '"py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }'
)


def main() -> int:
    source = ADAPTER.read_text(encoding="utf-8")
    first_line = source.splitlines()[0]
    assert first_line == PINNED_RUNNER, "adapter must start with the pinned Bradbury runner"
    assert "py-genlayer:test" not in source, "test runner alias is rejected on GenLayer networks"
    assert "py-genlayer:latest" not in source, "latest runner alias is rejected on GenLayer networks"
    assert "class MediChain(gl.Contract)" in source, "Bradbury runner expects gl.Contract"
    assert "@gl.contract" not in source, "Bradbury runner does not expose gl.contract"
    assert "gl.UserError" not in source, "pinned Bradbury runner does not expose gl.UserError"
    assert "raise Exception(message)" in source, "contract guards must use a supported exception"
    assert "sender_account" not in source, "pinned runner exposes sender_address, not sender_account"
    assert "self.owner = gl.message.sender_address" in source, "deployer must become relayer owner"
    assert "only the MediChain relayer can perform writes" in source
    assert "gl.nondet.web.render(" in source, "web operations must use the pinned runner API"
    assert "gl.nondet.exec_prompt(" in source, "LLM operations must use the pinned runner API"
    assert "gl.eq_principle.prompt_comparative(" in source
    assert "gl.get_webpage(" not in source, "legacy web calls fail in the pinned runner"
    assert "gl.eq_principle_prompt_" not in source, "legacy eq-principle calls fail in the pinned runner"
    assert "https://clinicaltrials.gov/api/v2/studies/" in source
    assert "_validate_protocol_snapshot(" in source
    register_method = source.split("def register_trial(", 1)[1].split(
        "@gl.public.write",
        1,
    )[0]
    assert "gl.nondet." not in register_method
    assert "protocol_snapshot_json: str" in register_method
    assert "emit_raw_event" not in source, "adapter should not depend on uncertain event API"
    assert "= TreeMap()" not in source, "bare TreeMap initializers block Bradbury deployment"

    tree = ast.parse(source)
    contract = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MediChain"
    )
    annotations = [
        ast.unparse(stmt.annotation)
        for stmt in contract.body
        if isinstance(stmt, ast.AnnAssign)
    ]
    assert "TreeMap[str, dict]" not in annotations, "dict storage blocks schema-safe deployment"
    assert "TreeMap[str, list]" not in annotations, "list storage blocks schema-safe deployment"
    assert "TreeMap[str, int]" not in annotations, "Bradbury storage requires bigint or sized integers"
    assert "Address" in annotations, "contract must store the treasury address"
    assert "TreeMap[str, u256]" in annotations, "bond storage must use u256"
    assert "TreeMap[str, bigint]" in annotations, "integer storage must use bigint"

    init = next(
        node for node in contract.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    init_annotations = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in init.args.args
        if arg.annotation is not None
    }
    assert init_annotations["treasury_address"] == "Address", "constructor must accept treasury address"

    for stmt in init.body:
        if not isinstance(stmt, ast.Assign):
            continue
        value = stmt.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        is_treemap_constructor = (
            isinstance(func, ast.Subscript)
            and isinstance(func.value, ast.Name)
            and func.value.id == "TreeMap"
        )
        assert not is_treemap_constructor, (
            "Bradbury initializes annotated TreeMap storage; do not assign TreeMap[...]() in __init__"
        )

    register_trial = next(
        node for node in contract.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_trial"
    )
    arg_annotations = {
        arg.arg: ast.unparse(arg.annotation)
        for arg in register_trial.args.args
        if arg.annotation is not None
    }
    assert arg_annotations["sponsor_wallet"] == "str", "TreeMap sponsor storage uses string addresses"
    assert arg_annotations["integrity_bond"] == "u256", "integrity_bond must be u256"

    write_methods = {"register_trial", "submit_results", "resolve_appeal", "submit_flag"}
    for method in contract.body:
        if not isinstance(method, ast.FunctionDef) or method.name not in write_methods:
            continue
        first_call = method.body[0]
        assert (
            isinstance(first_call, ast.Expr)
            and isinstance(first_call.value, ast.Call)
            and isinstance(first_call.value.func, ast.Attribute)
            and first_call.value.func.attr == "_require_owner"
        ), f"{method.name} must require the configured relayer owner first"

    print("GenLayer adapter Bradbury checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
