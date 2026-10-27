"""The verified session identity that scopes every tool call.

Security: role and category come from the session or identity provider only, never from the
chat or from model tool arguments (tool argument models forbid extra fields).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["buyer", "risk_manager", "director", "auditor"]
Category = Literal["electronics", "chemicals", "metals", "logistics"]


class UserContext(BaseModel):
    """Who is asking, as verified by the session.

    Attributes:
        user_id: Stable user id from the identity provider.
        role: App role.
        category: The buyer's category (row scope); ignored for the other roles.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@-]+$")
    role: Role
    category: Category | None = None

    @model_validator(mode="after")
    def _buyer_has_a_category(self) -> "UserContext":
        # Security: a buyer without a category would otherwise see every category.
        if self.role == "buyer" and self.category is None:
            raise ValueError("a buyer session needs a category")
        return self
