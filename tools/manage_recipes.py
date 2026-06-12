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
