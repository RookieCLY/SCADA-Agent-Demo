"""deployment — project-level validation & deployment lifecycle.

The actual deployment is a no-op in the demo; we record `validated` /
`deployed` / `failed` states + any validation errors so workflows can hinge on
"is the project deployable" without contacting a real target.

Validation rules implemented (matching the typical PDD / SCADA checklists):

  * every alarm must reference an existing point of the matching type
  * every widget binding must reference an existing point
  * every history config must reference an existing point
  * every on_change / on_alarm script must reference an existing point
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import ErrorCode, MockTool, ToolResult, fail, ok
from world import MockWorld
from world.models import Deployment

DOMAIN = "deployment"


def _collect_validation_errors(world: MockWorld) -> list[str]:
    errs: list[str] = []
    # alarms → points
    for a in world.alarms.values():
        if a.tag not in world.points:
            errs.append(f"alarm {a.id} references unknown tag {a.tag}")
            continue
        p = world.points[a.tag]
        if a.type != p.type:
            errs.append(
                f"alarm {a.id} type={a.type} but point {a.tag} type={p.type}"
            )
    # widget bindings → points
    for page in world.pages.values():
        for w in page.widgets.values():
            for prop, tag in w.bindings.items():
                if tag not in world.points:
                    errs.append(
                        f"widget {page.id}.{w.id}.{prop} bound to unknown point {tag}"
                    )
    # histories → points
    for h in world.histories.values():
        if h.tag not in world.points:
            errs.append(f"history config references unknown tag {h.tag}")
    # scripts → points (only when bound_tag is set)
    for s in world.scripts.values():
        if s.bound_tag and s.bound_tag not in world.points:
            errs.append(f"script {s.id} bound to unknown tag {s.bound_tag}")
    return errs


# ---------------------------------------------------------------- validate_project
class ValidateProjectArgs(BaseModel):
    action: Literal["validate_project"] = "validate_project"
    deployment_id: str = "default"
    target: str = "default"


class ValidateProject(MockTool):
    name = "validate_project"
    domain = DOMAIN
    action = "validate_project"
    description = "Run end-to-end consistency checks on the project (references, types)."
    args_model = ValidateProjectArgs
    examples = [
        "检查项目能不能下装",
        "validate the project before deploy",
        "整体校验一下配置",
        "跑一遍项目自检",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return []

    def run(self, args: ValidateProjectArgs, world: MockWorld) -> ToolResult:
        errs = _collect_validation_errors(world)
        record = world.deployments.get(args.deployment_id) or Deployment(
            id=args.deployment_id, target=args.target
        )
        record.validation_errors = errs
        record.status = "validated" if not errs else "failed"
        record.target = args.target
        world.deployments[args.deployment_id] = record
        return ok(
            data={"deployment_id": args.deployment_id, "errors": errs, "status": record.status},
            world_diff={
                "added_or_modified": {f"deployments.{args.deployment_id}": record.model_dump()},
                "removed": [],
            },
        )


# ---------------------------------------------------------------- deploy_project
class DeployProjectArgs(BaseModel):
    action: Literal["deploy_project"] = "deploy_project"
    deployment_id: str = "default"
    force: bool = Field(default=False, description="Deploy even if validation has not been run")


class DeployProject(MockTool):
    name = "deploy_project"
    domain = DOMAIN
    action = "deploy_project"
    description = "Mark the project as deployed. Requires a successful prior validation."
    args_model = DeployProjectArgs
    examples = [
        "把项目下发到目标",
        "deploy now",
        "下装项目到 PLC",
        "正式下装",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    def run(self, args: DeployProjectArgs, world: MockWorld) -> ToolResult:
        record = world.deployments.get(args.deployment_id)
        if record is None and not args.force:
            return fail(
                ErrorCode.BUSINESS_RULE,
                f"deployment {args.deployment_id} not validated — call validate_project first",
            )
        if record is not None and record.status == "failed" and not args.force:
            return fail(
                ErrorCode.BUSINESS_RULE,
                f"deployment {args.deployment_id} previously failed validation: "
                f"{record.validation_errors[:3]}",
            )
        if record is None:
            record = Deployment(id=args.deployment_id, target="default")
        record.status = "deployed"
        world.deployments[args.deployment_id] = record
        return ok(
            data={"deployment_id": args.deployment_id, "status": "deployed"},
            world_diff={
                "added_or_modified": {f"deployments.{args.deployment_id}.status": "deployed"},
                "removed": [],
            },
        )


# ---------------------------------------------------------------- rollback_deployment
class RollbackDeploymentArgs(BaseModel):
    action: Literal["rollback_deployment"] = "rollback_deployment"
    deployment_id: str = "default"
    notes: str = ""


class RollbackDeployment(MockTool):
    name = "rollback_deployment"
    domain = DOMAIN
    action = "rollback_deployment"
    description = "Roll the deployment back to draft and record a note."
    args_model = RollbackDeploymentArgs
    examples = [
        "回滚刚才的下装",
        "rollback the deployment",
        "撤销最后一次下装",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    def run(self, args: RollbackDeploymentArgs, world: MockWorld) -> ToolResult:
        if args.deployment_id not in world.deployments:
            return fail(
                ErrorCode.BUSINESS_RULE,
                f"deployment {args.deployment_id} not found",
            )
        record = world.deployments[args.deployment_id]
        record.status = "draft"
        record.notes = args.notes
        return ok(
            data={"deployment_id": args.deployment_id, "status": "draft"},
            world_diff={
                "added_or_modified": {
                    f"deployments.{args.deployment_id}.status": "draft",
                    f"deployments.{args.deployment_id}.notes": args.notes,
                },
                "removed": [],
            },
        )


# ---------------------------------------------------------------- show_deployment_status (read-only)
class ShowDeploymentStatusArgs(BaseModel):
    action: Literal["show_deployment_status"] = "show_deployment_status"
    deployment_id: str = "default"


class ShowDeploymentStatus(MockTool):
    name = "show_deployment_status"
    domain = DOMAIN
    action = "show_deployment_status"
    description = "Return the current deployment status and any validation errors."
    args_model = ShowDeploymentStatusArgs
    examples = [
        "项目当前状态如何",
        "show me the deployment status",
        "下装到哪一步了",
    ]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:
        return []

    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:
        return [f"deployments.{args.deployment_id}"]

    def run(self, args: ShowDeploymentStatusArgs, world: MockWorld) -> ToolResult:
        record = world.deployments.get(args.deployment_id)
        if record is None:
            return ok(data={"deployment_id": args.deployment_id, "status": "draft", "errors": []})
        return ok(data=record.model_dump())


# ---------------------------------------------------------------- registry hookup
DEPLOYMENT_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (
        ValidateProject,
        DeployProject,
        RollbackDeployment,
        ShowDeploymentStatus,
    )
}

DeploymentArgs = Annotated[
    Union[
        ValidateProjectArgs,
        DeployProjectArgs,
        RollbackDeploymentArgs,
        ShowDeploymentStatusArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN",
    "DEPLOYMENT_ACTIONS",
    "DeploymentArgs",
    "DeployProject",
    "DeployProjectArgs",
    "RollbackDeployment",
    "RollbackDeploymentArgs",
    "ShowDeploymentStatus",
    "ShowDeploymentStatusArgs",
    "ValidateProject",
    "ValidateProjectArgs",
]
