from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)

class ChangePasswordReq(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)

    @model_validator(mode="after")
    def validate_password_complexity(self):
        password = self.new_password
        if not (any(c.islower() for c in password) and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password) and any(not c.isalnum() for c in password)):
            raise ValueError("새 비밀번호에는 대문자·소문자·숫자·특수문자가 모두 필요합니다.")
        return self

class SharedVolumeMountReq(BaseModel):
    volume_id: int = Field(gt=0)
    mount_path: str = Field(min_length=2, max_length=200, pattern=r"^/[A-Za-z0-9/_-]+$")

    @model_validator(mode="after")
    def validate_mount_path(self):
        from pathlib import PurePosixPath
        normalized = PurePosixPath(self.mount_path)
        if ".." in self.mount_path.split("/") or str(normalized) in ("/", "/etc", "/proc", "/sys", "/dev", "/root"):
            raise ValueError("공유볼륨 마운트 경로는 안전한 전용 절대경로여야 합니다.")
        return self


class CreateInstanceReq(BaseModel):
    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    hostname: Optional[str] = Field(default=None, max_length=63, pattern=r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
    os_image_id: int = Field(gt=0)
    guest_username: str = Field(default="kvmadmin", min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$")
    guest_password: str = Field(min_length=12, max_length=128)
    vcores: int = Field(2, ge=1, le=4)
    memory_gb: int = Field(4, ge=1, le=8)
    root_disk_gb: int = Field(20, ge=10, le=100)
    extra_disk_gb: int = Field(0, ge=0, le=100)
    public_net_type: Optional[Literal["BRIDGED", "NAT"]] = "NAT"
    internal_network: bool = True
    management_network: bool = False
    shared_volumes: list[SharedVolumeMountReq] = Field(default_factory=list, max_length=8)

    @property
    def effective_hostname(self) -> str:
        return self.hostname or self.name

    @model_validator(mode="after")
    def validate_guest_credentials(self):
        if self.guest_username in ("root", "daemon", "nobody"):
            raise ValueError("게스트 관리자로 사용할 수 없는 사용자명입니다.")
        if not (any(x.islower() for x in self.guest_password) and any(x.isupper() for x in self.guest_password)
                and any(x.isdigit() for x in self.guest_password) and any(not x.isalnum() for x in self.guest_password)):
            raise ValueError("게스트 비밀번호는 대문자/소문자/숫자/특수문자를 포함해야 합니다.")
        return self

    @model_validator(mode="after")
    def validate_networks_and_extra_disk(self):
        if not self.public_net_type and not self.internal_network:
            raise ValueError("Public/NAT 또는 내부서비스 네트워크를 하나 이상 선택해야 합니다.")
        if self.shared_volumes and not self.management_network:
            raise ValueError('NFS 공유볼륨 사용 시 Management-Net을 선택해야 합니다.')
        if len({v.volume_id for v in self.shared_volumes}) != len(self.shared_volumes):
            raise ValueError('공유볼륨 중복 선택은 허용되지 않습니다.')
        if len({v.mount_path for v in self.shared_volumes}) != len(self.shared_volumes):
            raise ValueError('공유볼륨 마운트 경로가 중복되었습니다.')
        if 0 < self.extra_disk_gb < 10:
            raise ValueError("추가 디스크는 0 또는 10~100GB여야 합니다.")
        return self

class CreateDiskReq(BaseModel):
    disk_name: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    size_gb: int = Field(20, ge=10, le=100)

class ResizeInstanceReq(BaseModel):
    vcores: int = Field(ge=1, le=4)
    memory_gb: int = Field(ge=1, le=8)

class DiskActionReq(BaseModel):
    action: Literal["attach", "detach", "delete"]
    instance_id: Optional[int] = Field(default=None, gt=0)
    target_dev: str = Field(default="vdb", pattern=r"^vd[b-z]$")
