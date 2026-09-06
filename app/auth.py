import enum
from fastapi import Header, HTTPException, Depends


class Role(str, enum.Enum):
    agent = "agent"
    reporter = "reporter"


def get_current_role(x_role: str = Header(...)) -> Role:
    try:
        return Role(x_role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role header: {x_role}")


def require_agent(role: Role = Depends(get_current_role)) -> Role:
    if role != Role.agent:
        raise HTTPException(status_code=403, detail="Agent role required")
    return role
