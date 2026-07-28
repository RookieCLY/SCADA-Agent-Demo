"""manage_recipes — batch-process recipe configuration stubs.

Recipes define production sequences with steps, parameters, and transitions.
Common in chemical, pharma, and food/beverage automation.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from tools._base import MockTool, ToolResult, ok

DOMAIN = "manage_recipes"


# ---------------------------------------------------------------- create_recipe
class CreateRecipeArgs(BaseModel):
    action: Literal["create_recipe"] = "create_recipe"
    recipe_id: str = Field(description="Unique recipe identifier, e.g. 'rxn_batch_01'")
    recipe_name: str = Field(description="Display name, e.g. 'Reactor Batch Process'")
    product: str = Field(description="Target product name or code")
    description: str | None = None
    target_batch_size: float = Field(default=1000.0, gt=0, description="Target output quantity")


class CreateRecipe(MockTool):
    name = "create_recipe"
    domain = DOMAIN; action = "create_recipe"
    description = "Create a new batch-process recipe definition."
    args_model = CreateRecipeArgs
    examples = ["创建一个批次配方", "define a new batch recipe for reactor", "新建反应釜配方"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: CreateRecipeArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "created": True})


# ---------------------------------------------------------------- add_recipe_step
class AddRecipeStepArgs(BaseModel):
    action: Literal["add_recipe_step"] = "add_recipe_step"
    recipe_id: str
    step_id: str = Field(description="Unique step identifier, e.g. 'step_heat'")
    step_name: str = Field(description="Display name, e.g. 'Heat to 80C'")
    step_type: Literal["heat", "cool", "mix", "hold", "dose", "transfer", "wait", "check"] = "heat"
    target_setpoint: float | None = Field(default=None, description="Target value for this step")
    duration_s: float = Field(default=60.0, gt=0, description="Expected step duration in seconds")
    tolerance: float = Field(default=1.0, ge=0, description="Acceptable deviation from setpoint")


class AddRecipeStep(MockTool):
    name = "add_recipe_step"
    domain = DOMAIN; action = "add_recipe_step"
    description = "Add a processing step to an existing recipe."
    args_model = AddRecipeStepArgs
    examples = ["给配方添加升温步骤", "add heating step to recipe", "在批次流程中插入搅拌"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.steps.{args.step_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: AddRecipeStepArgs, world: object) -> ToolResult:
        return ok(data={"step_id": args.step_id, "added": True})


# ---------------------------------------------------------------- set_recipe_parameter
class SetRecipeParameterArgs(BaseModel):
    action: Literal["set_recipe_parameter"] = "set_recipe_parameter"
    recipe_id: str
    step_id: str
    param_name: str = Field(description="Parameter name, e.g. 'target_temp', 'stir_speed'")
    param_value: float
    unit: str | None = Field(default=None, description="Engineering unit, e.g. '°C', 'rpm'")


class SetRecipeParameter(MockTool):
    name = "set_recipe_parameter"
    domain = DOMAIN; action = "set_recipe_parameter"
    description = "Set a numeric parameter on a recipe step."
    args_model = SetRecipeParameterArgs
    examples = ["设置升温目标温度为80度", "set stir speed to 200 rpm", "调整配方参数"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.steps.{args.step_id}.params.{args.param_name}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.steps.{args.step_id}"]

    def run(self, args: SetRecipeParameterArgs, world: object) -> ToolResult:
        return ok(data={"parameter_set": True})


# ---------------------------------------------------------------- validate_recipe
class ValidateRecipeArgs(BaseModel):
    action: Literal["validate_recipe"] = "validate_recipe"
    recipe_id: str


class ValidateRecipe(MockTool):
    name = "validate_recipe"
    domain = DOMAIN; action = "validate_recipe"
    description = "Validate a recipe for completeness: all steps have parameters, no dead-ends."
    args_model = ValidateRecipeArgs
    examples = ["检查配方是否完整", "validate recipe before activation", "验证批次流程"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: ValidateRecipeArgs, world: object) -> ToolResult:
        return ok(data={"valid": True, "issues": []})


# ---------------------------------------------------------------- activate_recipe
class ActivateRecipeArgs(BaseModel):
    action: Literal["activate_recipe"] = "activate_recipe"
    recipe_id: str


class ActivateRecipe(MockTool):
    name = "activate_recipe"
    domain = DOMAIN; action = "activate_recipe"
    description = "Activate a recipe for production use."
    args_model = ActivateRecipeArgs
    examples = ["激活这个配方", "activate batch recipe", "启用配方开始生产"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.status"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: ActivateRecipeArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "activated": True})


# ---------------------------------------------------------------- clone_recipe
class CloneRecipeArgs(BaseModel):
    action: Literal["clone_recipe"] = "clone_recipe"
    source_recipe_id: str
    target_recipe_id: str = Field(description="Identifier for the new clone")
    target_name: str | None = None


class CloneRecipe(MockTool):
    name = "clone_recipe"
    domain = DOMAIN; action = "clone_recipe"
    description = "Create a deep copy of an existing recipe with a new ID."
    args_model = CloneRecipeArgs
    examples = ["复制配方", "clone recipe rxn_batch_01 as rxn_batch_02", "基于现有配方创建变体"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.target_recipe_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.source_recipe_id}"]

    def run(self, args: CloneRecipeArgs, world: object) -> ToolResult:
        return ok(data={"target_recipe_id": args.target_recipe_id, "cloned": True})


# ---------------------------------------------------------------- list_recipes
class ListRecipesArgs(BaseModel):
    action: Literal["list_recipes"] = "list_recipes"
    status: Literal["draft", "active", "inactive", "all"] = "all"
    page_size: int = Field(default=50, ge=1, le=200)


class ListRecipes(MockTool):
    name = "list_recipes"
    domain = DOMAIN; action = "list_recipes"
    description = "List recipes, optionally filtered by status."
    args_model = ListRecipesArgs
    examples = ["列出所有配方", "show me active recipes", "查询当前使用的配方"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ListRecipesArgs, world: object) -> ToolResult:
        return ok(data={"recipes": [], "count": 0})


# ---------------------------------------------------------------- registry hookup
RECIPE_ACTIONS: dict[str, type[MockTool]] = {
    cls.action: cls
    for cls in (CreateRecipe, AddRecipeStep, SetRecipeParameter, ValidateRecipe, ActivateRecipe, CloneRecipe, ListRecipes)
}

ManageRecipesArgs = Annotated[
    Union[
        CreateRecipeArgs, AddRecipeStepArgs, SetRecipeParameterArgs,
        ValidateRecipeArgs, ActivateRecipeArgs, CloneRecipeArgs, ListRecipesArgs,
    ],
    Field(discriminator="action"),
]


__all__ = [
    "DOMAIN", "ManageRecipesArgs", "RECIPE_ACTIONS",
    "CreateRecipe", "AddRecipeStep", "SetRecipeParameter",
    "ValidateRecipe", "ActivateRecipe", "CloneRecipe", "ListRecipes",
]


# ============================================================ extension tools
class DeleteRecipeArgs(BaseModel):
    action: Literal["delete_recipe"] = "delete_recipe"
    recipe_id: str


class DeleteRecipe(MockTool):
    name = "delete_recipe"
    domain = DOMAIN; action = "delete_recipe"
    description = "Delete a batch recipe."
    args_model = DeleteRecipeArgs
    examples = ["删除一个配方", "delete the old product recipe", "移除这个批次配方"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: DeleteRecipeArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "deleted": True})


class DeactivateRecipeArgs(BaseModel):
    action: Literal["deactivate_recipe"] = "deactivate_recipe"
    recipe_id: str


class DeactivateRecipe(MockTool):
    name = "deactivate_recipe"
    domain = DOMAIN; action = "deactivate_recipe"
    description = "Deactivate a recipe so it can no longer be downloaded to a batch."
    args_model = DeactivateRecipeArgs
    examples = ["停用这个配方", "deactivate the recipe before editing", "先把配方置为非激活"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.active"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: DeactivateRecipeArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "active": False})


class RemoveRecipeStepArgs(BaseModel):
    action: Literal["remove_recipe_step"] = "remove_recipe_step"
    recipe_id: str
    step_id: str


class RemoveRecipeStep(MockTool):
    name = "remove_recipe_step"
    domain = DOMAIN; action = "remove_recipe_step"
    description = "Remove a step from a batch recipe."
    args_model = RemoveRecipeStepArgs
    examples = ["删掉配方里的一个步骤", "remove the heating step from the recipe", "去掉这一步"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.steps.{args.step_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: RemoveRecipeStepArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "step_id": args.step_id, "removed": True})


class ReorderRecipeStepsArgs(BaseModel):
    action: Literal["reorder_recipe_steps"] = "reorder_recipe_steps"
    recipe_id: str
    ordered_step_ids: list[str] = Field(min_length=1)


class ReorderRecipeSteps(MockTool):
    name = "reorder_recipe_steps"
    domain = DOMAIN; action = "reorder_recipe_steps"
    description = "Change the execution order of the steps in a recipe."
    args_model = ReorderRecipeStepsArgs
    examples = ["调整配方步骤的顺序", "run the mixing step before heating", "重新排列配方步骤"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.step_order"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: ReorderRecipeStepsArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "steps": len(args.ordered_step_ids)})


class SetRecipeVersionArgs(BaseModel):
    action: Literal["set_recipe_version"] = "set_recipe_version"
    recipe_id: str
    version: str = Field(description="Semantic version, e.g. '2.1.0'")


class SetRecipeVersion(MockTool):
    name = "set_recipe_version"
    domain = DOMAIN; action = "set_recipe_version"
    description = "Stamp a recipe with a new version label for change control."
    args_model = SetRecipeVersionArgs
    examples = ["给配方打一个版本号", "bump the recipe version to 2.1", "更新配方版本"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.version"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: SetRecipeVersionArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "version": args.version})


class CompareRecipesArgs(BaseModel):
    action: Literal["compare_recipes"] = "compare_recipes"
    recipe_id_a: str
    recipe_id_b: str


class CompareRecipes(MockTool):
    name = "compare_recipes"
    domain = DOMAIN; action = "compare_recipes"
    description = "Diff two recipes (steps + parameters) and report the differences."
    args_model = CompareRecipesArgs
    examples = ["对比两个配方的差异", "compare the v1 and v2 recipes", "看看两个配方哪里不一样"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id_a}", f"recipes.{args.recipe_id_b}"]

    def run(self, args: CompareRecipesArgs, world: object) -> ToolResult:
        return ok(data={"differences": [], "count": 0})


class ExportRecipeArgs(BaseModel):
    action: Literal["export_recipe"] = "export_recipe"
    recipe_id: str
    format: Literal["json", "xml", "isa88"] = "json"


class ExportRecipe(MockTool):
    name = "export_recipe"
    domain = DOMAIN; action = "export_recipe"
    description = "Export a recipe to a portable file (JSON / XML / ISA-88)."
    args_model = ExportRecipeArgs
    examples = ["导出这个配方", "export the recipe as ISA-88", "把配方导出成文件"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: ExportRecipeArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "format": args.format})


class ImportRecipeArgs(BaseModel):
    action: Literal["import_recipe"] = "import_recipe"
    source_file: str
    new_recipe_id: str


class ImportRecipe(MockTool):
    name = "import_recipe"
    domain = DOMAIN; action = "import_recipe"
    description = "Import a recipe definition from a file into the recipe library."
    args_model = ImportRecipeArgs
    examples = ["从文件导入一个配方", "import the recipe supplied by the vendor", "把配方文件导进来"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.new_recipe_id}"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []

    def run(self, args: ImportRecipeArgs, world: object) -> ToolResult:
        return ok(data={"new_recipe_id": args.new_recipe_id, "imported": True})


class DownloadRecipeToBatchArgs(BaseModel):
    action: Literal["download_recipe_to_batch"] = "download_recipe_to_batch"
    recipe_id: str
    batch_id: str


class DownloadRecipeToBatch(MockTool):
    name = "download_recipe_to_batch"
    domain = DOMAIN; action = "download_recipe_to_batch"
    description = "Download an active recipe's setpoints to a running batch/unit."
    args_model = DownloadRecipeToBatchArgs
    examples = ["把配方下发到批次", "load this recipe into batch B-204", "下发配方参数到设备"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"batches.{args.batch_id}.recipe"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: DownloadRecipeToBatchArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "batch_id": args.batch_id})


class GetRecipeStatusArgs(BaseModel):
    action: Literal["get_recipe_status"] = "get_recipe_status"
    recipe_id: str


class GetRecipeStatus(MockTool):
    name = "get_recipe_status"
    domain = DOMAIN; action = "get_recipe_status"
    description = "Get a recipe's state (draft / validated / active) and last-used info."
    args_model = GetRecipeStatusArgs
    examples = ["查看配方的状态", "is this recipe validated and active", "配方现在是什么状态"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return []
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: GetRecipeStatusArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "status": "draft"})


class SetRecipeScalingArgs(BaseModel):
    action: Literal["set_recipe_scaling"] = "set_recipe_scaling"
    recipe_id: str
    batch_size: float = Field(gt=0)
    unit: str = "kg"


class SetRecipeScaling(MockTool):
    name = "set_recipe_scaling"
    domain = DOMAIN; action = "set_recipe_scaling"
    description = "Scale a recipe's quantities to a target batch size."
    args_model = SetRecipeScalingArgs
    examples = ["按批量缩放配方用量", "scale the recipe to a 500kg batch", "把配方放大到目标产量"]

    @staticmethod
    def intended_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}.scaling"]
    @staticmethod
    def referenced_entities(args: BaseModel) -> list[str]:  # pyright: ignore[reportArgumentType]
        return [f"recipes.{args.recipe_id}"]

    def run(self, args: SetRecipeScalingArgs, world: object) -> ToolResult:
        return ok(data={"recipe_id": args.recipe_id, "batch_size": args.batch_size})


RECIPE_ACTIONS.update({
    cls.action: cls
    for cls in (
        DeleteRecipe, DeactivateRecipe, RemoveRecipeStep, ReorderRecipeSteps,
        SetRecipeVersion, CompareRecipes, ExportRecipe, ImportRecipe,
        DownloadRecipeToBatch, GetRecipeStatus, SetRecipeScaling,
    )
})
