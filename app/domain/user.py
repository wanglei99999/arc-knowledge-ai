from __future__ import annotations

import uuid
from dataclasses import dataclass,field 
from datetime import datetime,timezone 

@dataclass
class User:
    tenant_id:str
    email:str
    hashed_password:str
    user_id: str = field(default_factory=lambda:str(uuid.uuid4()))
    is_active:bool = True
    created_at:datetime = field(default_factory=lambda:datetime.now(timezone.utc))