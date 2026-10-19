from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["registered", "in_use", "consumed", "discarded"]


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=50)
    location: Optional[str] = None


class Device(DeviceIn):
    id: int


class SampleIn(BaseModel):
    barcode: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    location: Optional[str] = None


class SampleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[Status] = None
    location: Optional[str] = None


class Sample(SampleIn):
    id: int
    status: Status
    created_at: str


class RunIn(BaseModel):
    sample_barcode: str
    device_id: int
    result: dict[str, Any]


class Run(BaseModel):
    id: int
    sample_id: int
    device_id: int
    result: dict[str, Any]
    measured_at: str
