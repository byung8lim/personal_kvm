import os, uuid, psutil, hashlib, threading, logging, time, json
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request, Body
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text, func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.init_db import init_seed_data
from app.core.security import verify_password, create_access_token, get_password_hash, require_admin, require_active_admin
from app.models.entities import AdminUser, OsImage, StoragePool, VirtualNetwork, IpPool, Instance, InstanceNic, VirtualDisk, ResourceOperation, ResourceLock, ManagedLbService, ManagedNfsVolume, ManagedNfsMount
from app.services.instance_state import InvalidStateTransition, ensure_transition
from app.services.libvirt_service import LibvirtError, libvirt_service
from app.services.vm_provisioner import ProvisioningError, VmProvisioner, resolve_qemu_emulator
from app.services.storage_service import synchronize_storage_pools
from app.services.image_worker import image_worker
from app.services.bind9_ddns import Bind9Ddns, DdnsError
from app.services.dns_inspector import dns_status
from app.services.haproxy_l4 import render_tcp_service
from app.services.traefik_l7 import render_yaml as render_l7_yaml
from app.services.nfs_volumes import create_volume, delete_volume, VolumeError
from app.schemas.requests import LoginReq, ChangePasswordReq, CreateInstanceReq, CreateDiskReq, ResizeInstanceReq, DiskActionReq
from app.core.runtime_config import runtime_config, storage_path

vm_provisioner = VmProvisioner(libvirt_service)


def configured_ddns():
    """Return the optional TSIG updater; no DDNS calls when disabled."""
    dns = runtime_config().get("dns") or {}
    if not dns.get("enabled", False):
        return None
    required = ("server", "key_file", "forward_zone", "reverse_zone")
    missing = [key for key in required if not dns.get(key)]
    if missing:
        raise DdnsError("Bind9 DNS 필수 설정 누락: " + ", ".join(missing))
    return Bind9Ddns(dns["server"], dns["key_file"], dns["forward_zone"],
                     dns["reverse_zone"], ttl=int(dns.get("ttl", 300)))

app = FastAPI(title="퍼스널 KVM 가상화 서비스 API", version="0.0.8")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=jsonable_encoder({"code": "VALIDATION_ERROR", "detail": "입력값이 유효하지 않습니다.", "errors": exc.errors()}))

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

@app.on_event("startup")
def startup_event():
    db = SessionLocal()
    try:
        init_seed_data(db)
        # A process restart can interrupt synchronous provisioning after its
        # durable CREATING checkpoint. Reconcile it instead of leaking a
        # forever-CREATING row or invisible external resources.
        creating_ids = [row.id for row in db.query(Instance).filter(Instance.state == "CREATING").all()]
        for instance_id in creating_ids:
            inst = db.query(Instance).filter(Instance.id == instance_id).one()
            operation = db.query(ResourceOperation).filter(
                ResourceOperation.resource_id == instance_id,
                ResourceOperation.action == "CREATE",
                ResourceOperation.status == "RUNNING",
            ).order_by(ResourceOperation.id.desc()).first()
            if not operation:
                continue
            if libvirt_service.domain_exists(inst.name):
                actual = libvirt_service.domain_state(inst.name)
                if actual == "RUNNING":
                    inst.state = "RUNNING"
                    finish_operation(operation)
                    db.commit()
                    continue
            disk_paths = [row.file_path for row in db.query(VirtualDisk).filter(
                VirtualDisk.instance_id == instance_id
            ).all()]
            disk_paths.append(str(storage_path("root_disk_dir") / f"{inst.name}-seed.iso"))
            vm_provisioner.cleanup(inst.name, disk_paths,
                                   domain_defined=libvirt_service.domain_exists(inst.name))
            finalize_failed_creation(db, instance_id, operation.operation_uuid,
                                     "서비스 재시작으로 생성 작업이 중단되어 자원을 롤백했습니다.")

        for inst in db.query(Instance).filter(
            Instance.state.notin_(("DELETED", "CREATING"))
        ).all():
            try:
                actual = libvirt_service.domain_state(inst.name)
                if inst.last_desired_state == "RUNNING" and actual == "STOPPED":
                    libvirt_service.start(inst.name)
                    actual = libvirt_service.domain_state(inst.name)
                libvirt_service.autostart(inst.name, inst.last_desired_state == "RUNNING")
                inst.state = actual
                inst.error_message = None
            except LibvirtError as exc:
                inst.state = "ERROR"
                inst.error_message = f"부팅 상태 복구 실패: {exc}"
        db.commit()
    finally:
        db.close()
    image_worker.start()

@app.on_event("shutdown")
def shutdown_event():
    image_worker.stop()

def begin_operation(db: Session, inst: Instance, action: str, target_state: str) -> ResourceOperation:
    op = ResourceOperation(
        operation_uuid=str(uuid.uuid4()),
        resource_type="INSTANCE",
        resource_id=inst.id,
        action=action.upper(),
        previous_state=inst.state,
        target_state=target_state,
        status="RUNNING",
    )
    db.add(op)
    db.flush()
    return op

def finish_operation(op: ResourceOperation, error: str = None) -> None:
    op.status = "FAILED" if error else "SUCCEEDED"
    op.error_message = error
    op.completed_at = datetime.utcnow()

def finalize_failed_creation(db: Session, instance_id: int, operation_uuid: str, error: str) -> None:
    """Release reservations while preserving a visible ERROR instance."""
    instance = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
    for disk in db.query(VirtualDisk).filter(VirtualDisk.instance_id == instance_id).all():
        pool = db.query(StoragePool).filter(StoragePool.id == disk.pool_id).with_for_update().one()
        pool.allocated_size_gb = max(0, float(pool.allocated_size_gb) - disk.size_gb)
        pool.available_size_gb = min(float(pool.total_size_gb), float(pool.available_size_gb) + disk.size_gb)
        db.delete(disk)
    for ip_row in db.query(IpPool).filter(IpPool.instance_id == instance_id).all():
        ip_row.status = "AVAILABLE"
        ip_row.instance_id = None
        ip_row.assigned_at = None
        network = db.query(VirtualNetwork).filter(VirtualNetwork.id == ip_row.network_id).with_for_update().one()
        network.assigned_ips = max(0, (network.assigned_ips or 0) - 1)
    db.query(InstanceNic).filter(InstanceNic.instance_id == instance_id).delete(synchronize_session=False)
    instance.public_ip = None
    instance.private_ip = None
    instance.mgmt_ip = None
    instance.state = "ERROR"
    instance.last_desired_state = "STOPPED"
    instance.error_message = error[:2000]
    operation = db.query(ResourceOperation).filter(
        ResourceOperation.operation_uuid == operation_uuid
    ).with_for_update().one()
    finish_operation(operation, error[:2000])
    db.commit()

def remove_managed_disk_file(file_path: str) -> None:
    candidate = Path(file_path).resolve()
    roots = (storage_path("root_disk_dir").resolve(), storage_path("extra_disk_dir").resolve())
    if not any(candidate == root or root in candidate.parents for root in roots):
        raise RuntimeError(f"관리 스토리지 외부 파일은 삭제할 수 없습니다: {candidate}")
    if candidate.exists():
        candidate.unlink()

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.0.6"}

@app.get("/api/health/ready")
def readiness():
    checks = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = str(exc)
    libvirt = libvirt_service.connection_status()
    checks["libvirt"] = "ok" if libvirt.get("connected") else libvirt.get("error", "unavailable")
    try:
        checks["qemu"] = "ok"
        checks["qemu_path"] = resolve_qemu_emulator(settings.QEMU_EMULATOR_PATH or None)
    except ProvisioningError as exc:
        checks["qemu"] = str(exc)
    key_path = Path(vm_provisioner.ssh_public_key_path)
    checks["ssh_public_key"] = "ok" if key_path.is_file() else f"missing: {key_path}"
    for key in ("image_dir", "root_disk_dir", "extra_disk_dir"):
        path = storage_path(key)
        checks[key] = "ok" if path.is_dir() and os.access(path, os.W_OK) else f"not writable: {path}"
    for network_name, network in (runtime_config().get("networks") or {}).items():
        bridge = network.get("bridge_name")
        check_name = f"bridge_{network_name}"
        checks[check_name] = "ok" if bridge and Path("/sys/class/net", bridge).exists() else f"missing: {bridge}"
    ready = all(value == "ok" or key == "qemu_path" for key, value in checks.items())
    payload = {"status": "ready" if ready else "not_ready", "version": "0.0.6", "checks": checks}
    return payload if ready else JSONResponse(status_code=503, content=payload)

@app.get("/api/v1/lb/services")
def list_lb_services(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    return [{"id":v.id, "name":v.name, "layer":v.layer, "status":v.status,
             "specification":json.loads(v.specification), "error_message":v.error_message}
            for v in db.query(ManagedLbService).order_by(ManagedLbService.id.desc()).all()]

@app.post("/api/v1/lb/services", status_code=201)
def create_lb_service(spec: dict = Body(...), db: Session = Depends(get_db),
                      _admin: str = Depends(require_active_admin)):
    layer=spec.get("layer")
    try:
        if layer=="L4": rendered=render_tcp_service(spec)
        elif layer=="L7": rendered=render_l7_yaml(spec)
        else: raise ValueError("layer must be L4 or L7")
    except (TypeError,ValueError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc))
    if db.query(ManagedLbService).filter(ManagedLbService.name==spec["name"]).first():
        raise HTTPException(status_code=409,detail="Load balancer name already exists")
    record=ManagedLbService(name=spec["name"],layer=layer,
                            specification=json.dumps(spec,ensure_ascii=False),status="DRAFT")
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id":record.id,"name":record.name,"status":record.status,"preview":rendered}

@app.delete("/api/v1/lb/services/{service_id}")
def delete_lb_service(service_id: int, db: Session = Depends(get_db),
                      _admin: str = Depends(require_active_admin)):
    record=db.query(ManagedLbService).filter(ManagedLbService.id==service_id).with_for_update().first()
    if not record: raise HTTPException(status_code=404,detail="Load balancer not found")
    if record.status not in ("DRAFT","ERROR"):
        raise HTTPException(status_code=409,detail="Disable routing before deleting an active LB")
    db.delete(record)
    db.commit()
    return {"status":"deleted"}

@app.get("/api/v1/nfs/volumes")
def get_nfs_volumes(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    return [{"id":v.id,"name":v.name,"path":v.file_path,"status":v.status,
             "mount_count":db.query(ManagedNfsMount).filter(ManagedNfsMount.volume_id==v.id).count()}
            for v in db.query(ManagedNfsVolume).order_by(ManagedNfsVolume.id).all()]

@app.post("/api/v1/nfs/volumes", status_code=201)
def add_nfs_volume(payload: dict = Body(...), db: Session = Depends(get_db),
                   _admin: str = Depends(require_active_admin)):
    name=payload.get("name")
    spec=runtime_config().get("nfs",{})
    root=spec.get("NFS_DIR")
    if not root: raise HTTPException(status_code=503,detail="NFS_DIR is not configured")
    if db.query(ManagedNfsVolume).filter(ManagedNfsVolume.name==name).first():
        raise HTTPException(status_code=409,detail="Volume already registered")
    try:
        path=create_volume(root,name)
    except (VolumeError,OSError,TypeError) as exc:
        raise HTTPException(status_code=422,detail=str(exc))
    v=ManagedNfsVolume(name=name,file_path=path,status="READY")
    try:
        db.add(v)
        db.commit()
        db.refresh(v)
    except Exception:
        db.rollback()
        # Never silently destroy files on DB failure.
        raise
    return {"id":v.id,"name":v.name,"path":path}

@app.delete("/api/v1/nfs/volumes/{volume_id}")
def remove_nfs_volume(volume_id:int,db: Session = Depends(get_db),
                      _admin: str = Depends(require_active_admin)):
    volume=db.query(ManagedNfsVolume).filter(ManagedNfsVolume.id==volume_id).with_for_update().first()
    if not volume: raise HTTPException(status_code=404,detail="Volume not found")
    users=db.query(ManagedNfsMount).filter(ManagedNfsMount.volume_id==volume.id).count()
    try: delete_volume(runtime_config().get("nfs",{}).get("NFS_DIR"),volume.name,users)
    except (VolumeError,OSError,TypeError) as exc:
        raise HTTPException(status_code=409,detail=str(exc))
    db.delete(volume)
    db.commit()
    return {"status":"deleted"}

@app.post("/api/v1/l7/preview")
def preview_l7_configuration(spec: dict = Body(...),_admin: str = Depends(require_active_admin)):
    try: return {"status":"preview_only","configuration":render_l7_yaml(spec)}
    except (ValueError,TypeError,KeyError) as exc:
        raise HTTPException(status_code=422,detail=str(exc))

@app.post("/api/v1/l4/preview")
def preview_l4_configuration(spec: dict = Body(...), _admin: str = Depends(require_active_admin)):
    """Generate a candidate HAProxy snippet without applying host changes."""
    try:
        rendered = render_tcp_service(spec)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "preview_only", "configuration": rendered}

@app.get("/api/v1/dns/status")
def get_dns_status(_admin: str = Depends(require_active_admin)):
    """Read-only SOA status for configured DNS zones; never exposes key material."""
    return dns_status(runtime_config().get("dns") or {})

@app.get("/api/v1/system/libvirt")
def get_libvirt_status(_admin: str = Depends(require_active_admin)):
    return libvirt_service.connection_status()

@app.get("/api/v1/operations")
def list_operations(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    rows = db.query(ResourceOperation).order_by(ResourceOperation.id.desc()).limit(200).all()
    return [{"id": r.id, "operation_uuid": r.operation_uuid, "resource_type": r.resource_type,
             "resource_id": r.resource_id, "action": r.action, "previous_state": r.previous_state,
             "target_state": r.target_state, "status": r.status, "error_message": r.error_message,
             "started_at": r.started_at, "completed_at": r.completed_at} for r in rows]

@app.post("/api/v1/auth/login")
def login(req: LoginReq, db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    user.last_login_at = datetime.utcnow()
    db.commit()
    return {"access_token": create_access_token(user.username), "token_type": "bearer", "username": user.username,
            "must_change_password": bool(user.must_change_password)}

@app.post("/api/v1/auth/change-password")
def change_password(req: ChangePasswordReq, username: str = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not user or not verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if verify_password(req.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="새 비밀번호는 현재 비밀번호와 달라야 합니다.")
    user.password_hash = get_password_hash(req.new_password)
    user.must_change_password = 0
    db.commit()
    return {"status": "ok"}

@app.get("/api/v1/dashboard")
def get_dashboard(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    cpu_cores = psutil.cpu_count(logical=True) or 8
    mem_total_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    instances = db.query(Instance).filter(Instance.state != "DELETED").all()
    actual_domains = []
    for instance in instances:
        try:
            actual_domains.append(libvirt_service.domain_info(instance.name))
        except LibvirtError:
            actual_domains.append({"vcores": instance.vcores, "memory_gb": instance.memory_gb, "state": "ERROR"})
    alloc_vcores = sum(int(item["vcores"]) for item in actual_domains)
    alloc_mem_gb = round(sum(float(item["memory_gb"]) for item in actual_domains), 2)
    pools = synchronize_storage_pools(db)
    nets = db.query(VirtualNetwork).all()
    return {
        "host": {
            "cpu_cores": cpu_cores,
            "max_vcores": cpu_cores * 2,
            "allocated_vcores": alloc_vcores,
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_total_gb": mem_total_gb,
            "allocated_memory_gb": alloc_mem_gb,
            "available_memory_gb": max(0, round(mem_total_gb - settings.RESERVED_HOST_MEMORY_GB - alloc_mem_gb, 1)),
            "reserved_host_memory_gb": settings.RESERVED_HOST_MEMORY_GB,
            "memory_percent": psutil.virtual_memory().percent,
        },
        "instance_counts": {
            "total": len(instances),
            "running": sum(1 for i in actual_domains if i["state"] == "RUNNING"),
            "stopped": sum(1 for i in actual_domains if i["state"] == "STOPPED"),
        },
        "storage_pools": pools,
        "networks": [
            {"id": n.id, "net_name": n.net_name, "net_type": n.net_type, "bridge_name": n.bridge_name, "cidr": n.cidr, "gateway_ip": n.gateway_ip, "total_ips": n.total_ips, "assigned_ips": n.assigned_ips}
            for n in nets
        ]
    }

@app.get("/api/v1/instances")
def list_instances(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    items = db.query(Instance).filter(Instance.state != "DELETED").order_by(Instance.id.desc()).all()
    images = {img.id: f"{img.linux_type} {img.version}" for img in db.query(OsImage).all()}
    for item in items:
        if item.state in {"CREATING", "STARTING", "STOPPING", "RESIZING", "DELETING"} or (item.state == "ERROR" and not libvirt_service.domain_exists(item.name)):
            continue
        try:
            actual_state = libvirt_service.domain_state(item.name)
            if item.state != actual_state:
                item.state = actual_state
                item.error_message = None if actual_state != "ERROR" else item.error_message
        except LibvirtError as exc:
            item.state = "ERROR"
            item.error_message = item.error_message or f"libvirt 도메인 상태 조회 실패: {exc}"
    db.commit()
    return [
        {
            "id": i.id, "instance_uuid": i.instance_uuid, "name": i.name,
            "os_name": images.get(i.os_image_id, "Rocky 9.8"),
            "dns_hostname": i.dns_hostname, "vcores": i.vcores, "memory_gb": i.memory_gb,
            "root_disk_gb": i.root_disk_gb, "extra_disk_gb": i.extra_disk_gb,
            "public_ip": i.public_ip, "private_ip": i.private_ip, "mgmt_ip": i.mgmt_ip,
            "state": i.state, "error_message": i.error_message,
            "created_at": i.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }
        for i in items
    ]

# VM creation does not hold the HTTP request open. The committed DB row is
# the authoritative job record; workers use their own SQLAlchemy sessions.
_CREATE_PHASES = {"QUEUED": "생성 대기", "ROOT_DISK": "루트 디스크 생성", "OS_CUSTOMIZE": "OS 초기화", "EXTRA_DISK": "추가 디스크 생성", "CLOUD_INIT": "계정 및 네트워크 구성", "DEFINE": "VM 등록", "BOOT": "VM 부팅", "NETWORK": "네트워크 확인", "DNS": "내부 DNS 등록", "FINALIZE": "결과 저장"}

def _create_worker(instance_id, operation_uuid, instance_name, image_path, root_path, root_gb,
                   extra_path, extra_gb, memory_gb, vcores, bridges, macs, network_configs,
                   seed_path, guest_username, guest_password, dns_hostname, shared_mounts, nfs_package):
    # Never share a request-scoped Session across threads.
    session = SessionLocal()
    provisioned = False
    dns_registered = False
    paths = [x for x in (root_path, extra_path, seed_path) if x]
    def progress(phase, description):
        try:
            op = session.query(ResourceOperation).filter(ResourceOperation.operation_uuid == operation_uuid).one()
            op.previous_state = phase  # operation-local progress marker (existing column)
            session.commit()
        except Exception:
            session.rollback()
            logging.exception("VM %s progress update failed (%s)", instance_name, phase)
    try:
        progress("ROOT_DISK", "루트 디스크 생성 중")
        vm_provisioner.provision(instance_name, image_path, root_path, root_gb, extra_path,
                                 extra_gb, memory_gb, vcores, bridges, macs, network_configs,
                                 seed_path, guest_username, guest_password, progress=progress,
                                 guest_hostname=dns_hostname, shared_mounts=shared_mounts, nfs_package=nfs_package)
        provisioned = True
        instance = session.query(Instance).filter(Instance.id == instance_id).one()
        updater = configured_ddns()
        if updater and instance.private_ip:
            progress("DNS", "내부 A/PTR 등록 중")
            updater.add(instance.dns_hostname, instance.private_ip)
            dns_registered = True
        progress("FINALIZE", "생성 결과 기록 중")
        instance = session.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
        op = session.query(ResourceOperation).filter(ResourceOperation.operation_uuid == operation_uuid).with_for_update().one()
        instance.state = "RUNNING"
        instance.error_message = None
        finish_operation(op)
        session.commit()
    except Exception as exc:
        session.rollback()
        logging.exception("VM %s creation failed", instance_name)
        if dns_registered:
            try:
                configured_ddns().remove(dns_hostname, instance.private_ip)
            except Exception:
                logging.exception("VM %s DNS compensation failed", instance_name)
        # Provisioner compensates its own failures; cleanup again is idempotent
        # and covers failures while recording final DB state.
        try:
            vm_provisioner.cleanup(instance_name, paths, domain_defined=libvirt_service.domain_exists(instance_name))
        except Exception:
            logging.exception("VM %s cleanup failed", instance_name)
        try:
            finalize_failed_creation(session, instance_id, operation_uuid, f"{type(exc).__name__}: {exc}")
        except Exception:
            session.rollback()
            logging.exception("VM %s failure recording failed", instance_name)
    finally:
        session.close()

@app.get("/api/v1/instance-operations")
def list_instance_operations(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    rows = db.query(ResourceOperation).filter(ResourceOperation.resource_type == "INSTANCE", ResourceOperation.action == "CREATE").order_by(ResourceOperation.id.desc()).limit(100).all()
    return [{"id": x.id, "instance_id": x.resource_id, "operation_uuid": x.operation_uuid,
             "status": x.status, "phase": x.previous_state if x.status == "RUNNING" else x.status,
             "message": _CREATE_PHASES.get(x.previous_state, x.previous_state or "생성 처리 중") if x.status == "RUNNING" else ("생성 완료" if x.status == "SUCCEEDED" else "생성 실패"),
             "error_message": x.error_message, "started_at": x.started_at.isoformat() if x.started_at else None,
             "completed_at": x.completed_at.isoformat() if x.completed_at else None} for x in rows]

@app.post("/api/v1/instances", status_code=202)
def create_instance(req: CreateInstanceReq, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    if 0 < req.extra_disk_gb < 10:
        raise HTTPException(status_code=422, detail="추가 디스크는 0 또는 10~100GB여야 합니다.")
    if not req.public_net_type and not req.internal_network:
        raise HTTPException(status_code=422, detail="Public/NAT 또는 내부서비스 네트워크를 하나 이상 선택해야 합니다.")
    image = db.query(OsImage).filter(OsImage.id == req.os_image_id, OsImage.status == "READY").first()
    if not image or not Path(image.file_path).is_file():
        raise HTTPException(status_code=409, detail="사용 가능한 OS 이미지 파일이 없습니다.")
    if image.checksum_sha256:
        digest = hashlib.sha256()
        with Path(image.file_path).open("rb") as image_file:
            for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != image.checksum_sha256:
            raise HTTPException(status_code=409, detail="OS 이미지 체크섬 검증에 실패했습니다.")
    if db.query(Instance).filter(Instance.name == req.name, Instance.state != "DELETED").first():
        raise HTTPException(status_code=400, detail="이미 존재하는 인스턴스 이름입니다.")
    if db.query(Instance).filter(Instance.dns_hostname == req.effective_hostname, Instance.state != "DELETED").first():
        raise HTTPException(status_code=409, detail="이미 사용 중인 내부 DNS 호스트명입니다.")
    if libvirt_service.domain_exists(req.name):
        raise HTTPException(status_code=409, detail="동일한 libvirt 도메인 이름이 이미 존재합니다.")
    deleted_same_name = db.query(Instance).filter(Instance.name == req.name, Instance.state == "DELETED").first()
    if deleted_same_name:
        suffix = f".deleted.{deleted_same_name.id}"
        deleted_same_name.name = req.name[:64-len(suffix)] + suffix
        db.flush()
    db.query(ResourceLock).filter(ResourceLock.id == 1).with_for_update().one()
    active_instances = db.query(Instance).filter(Instance.state != "DELETED").all()
    max_vcores = (psutil.cpu_count(logical=True) or 1) * 2
    max_memory = max(1, int(psutil.virtual_memory().total / (1024 ** 3)) - settings.RESERVED_HOST_MEMORY_GB)
    if sum(i.vcores for i in active_instances) + req.vcores > max_vcores:
        db.rollback(); raise HTTPException(status_code=409, detail="호스트 VCore 할당 한도를 초과합니다.")
    if sum(i.memory_gb for i in active_instances) + req.memory_gb > max_memory:
        db.rollback(); raise HTTPException(status_code=409, detail="호스트 예약 메모리를 제외한 할당 한도를 초과합니다.")
    pub_net = db.query(VirtualNetwork).filter(VirtualNetwork.net_type == req.public_net_type).first() if req.public_net_type else None
    int_net = db.query(VirtualNetwork).filter(VirtualNetwork.net_type == "INTERNAL").first() if req.internal_network else None
    mgmt_net = db.query(VirtualNetwork).filter(VirtualNetwork.net_type == "MANAGEMENT").first() if req.management_network else None

    ip_strategy = runtime_config().get("service", {}).get("ip_allocation_strategy", "RANDOM")
    if ip_strategy not in ("RANDOM", "SERIAL"):
        db.rollback()
        raise HTTPException(status_code=422, detail="service.ip_allocation_strategy는 RANDOM 또는 SERIAL이어야 합니다.")

    def alloc_ip(net):
        if not net: return None
        candidates = db.query(IpPool).filter(IpPool.network_id == net.id, IpPool.status == "AVAILABLE")
        if ip_strategy == "RANDOM":
            candidates = candidates.order_by(func.rand())
        else:
            candidates = candidates.order_by(func.inet_aton(IpPool.ip_address))
        ip_row = candidates.with_for_update(skip_locked=True).first()
        if ip_row:
            ip_row.status = "ASSIGNED"
            ip_row.assigned_at = datetime.utcnow()
            net.assigned_ips = (net.assigned_ips or 0) + 1
            return ip_row
        return None

    pub_ip = alloc_ip(pub_net)
    priv_ip = alloc_ip(int_net)
    mgmt_ip = alloc_ip(mgmt_net)
    if (pub_net and not pub_ip) or (int_net and not priv_ip) or (mgmt_net and not mgmt_ip):
        db.rollback()
        raise HTTPException(status_code=409, detail="필수 네트워크 IP 풀이 고갈되었습니다.")

    shared_mounts = []
    if req.shared_volumes:
        if not mgmt_net or not mgmt_ip:
            db.rollback()
            raise HTTPException(status_code=422, detail="공유볼륨에는 Management-Net이 필요합니다.")
        nfs_root = (runtime_config().get("nfs") or {}).get("NFS_DIR")
        nfs_host = (runtime_config().get("networks") or {}).get("management", {}).get("host_bind_ip")
        if not nfs_root or not nfs_host:
            db.rollback()
            raise HTTPException(status_code=503, detail="NFS_DIR 또는 Management-Net 서버 IP가 설정되지 않았습니다.")
        for mount in req.shared_volumes:
            volume = db.query(ManagedNfsVolume).filter(
                ManagedNfsVolume.id == mount.volume_id,
                ManagedNfsVolume.status == "READY").first()
            if not volume:
                db.rollback()
                raise HTTPException(status_code=404, detail=f"공유볼륨 {mount.volume_id}을 찾을 수 없습니다.")
            shared_mounts.append({"volume_id":volume.id, "target":mount.mount_path,
                                  "source":f"{nfs_host}:{volume.file_path}"})

    inst = Instance(
        instance_uuid=str(uuid.uuid4()),
        name=req.name,
        dns_hostname=req.effective_hostname,
        os_image_id=req.os_image_id,
        vcores=req.vcores,
        memory_gb=req.memory_gb,
        root_disk_gb=req.root_disk_gb,
        extra_disk_gb=req.extra_disk_gb,
        public_ip=pub_ip.ip_address if pub_ip else None,
        private_ip=priv_ip.ip_address if priv_ip else None,
        mgmt_ip=mgmt_ip.ip_address if mgmt_ip else None,
        state="CREATING",
        last_desired_state="RUNNING"
    )
    db.add(inst)
    db.flush()
    for mount in shared_mounts:
        db.add(ManagedNfsMount(volume_id=mount["volume_id"], instance_id=inst.id,
                               target_path=mount["target"], status="PLANNED"))
    for ip_r in [pub_ip, priv_ip, mgmt_ip]:
        if ip_r:
            ip_r.instance_id = inst.id
    selected = [(pub_net, pub_ip, 1), (int_net, priv_ip, 0), (mgmt_net, mgmt_ip, 0)]
    selected = [(net, ip, default) for net, ip, default in selected if net and ip]
    macs = []
    for index, (net, ip_row, default) in enumerate(selected):
        raw = uuid.uuid4().hex[:10]
        mac = "52:54:" + ":".join(raw[i:i+2] for i in range(0, 8, 2))
        macs.append(mac)
        db.add(InstanceNic(instance_id=inst.id, network_id=net.id, ip_pool_id=ip_row.id,
                           iface_name=f"eth{index}", mac_address=mac, is_default_gw=default))

    root_pool = db.query(StoragePool).filter(StoragePool.pool_type == "ROOT").with_for_update().first()
    extra_pool = db.query(StoragePool).filter(StoragePool.pool_type == "EXTRA").with_for_update().first()
    if not root_pool or (req.extra_disk_gb and not extra_pool):
        db.rollback()
        raise HTTPException(status_code=409, detail="필요한 스토리지 풀이 없습니다.")
    required_root = req.root_disk_gb
    required_extra = req.extra_disk_gb
    if float(root_pool.available_size_gb) < required_root or (required_extra and float(extra_pool.available_size_gb) < required_extra):
        db.rollback()
        raise HTTPException(status_code=409, detail="스토리지 가용 용량이 부족합니다.")
    root_path = str(Path(root_pool.mount_path) / f"{req.name}-root.qcow2")
    extra_path = str(Path(extra_pool.mount_path) / f"{req.name}-extra.qcow2") if req.extra_disk_gb else ""
    root_disk = VirtualDisk(
            disk_uuid=str(uuid.uuid4()), disk_name=f"{req.name}-root", pool_id=root_pool.id,
            disk_role="ROOT", size_gb=req.root_disk_gb, file_path=root_path,
            is_assigned=1, instance_id=inst.id, target_dev="vda", status="IN_USE")
    db.add(root_disk)
    extra_disk = None
    if req.extra_disk_gb and req.extra_disk_gb >= 10:
        extra_disk = VirtualDisk(
                disk_uuid=str(uuid.uuid4()), disk_name=f"{req.name}-extra", pool_id=extra_pool.id,
                disk_role="EXTRA", size_gb=req.extra_disk_gb, file_path=extra_path,
                is_assigned=1, instance_id=inst.id, target_dev="vdb", status="IN_USE")
        db.add(extra_disk)
    operation_uuid = str(uuid.uuid4())
    operation = ResourceOperation(
        operation_uuid=operation_uuid,
        resource_type="INSTANCE",
        resource_id=inst.id,
        action="CREATE",
        target_state="RUNNING",
        status="RUNNING",
        previous_state="QUEUED",
    )
    db.add(operation)
    # SessionLocal disables autoflush.  Keep the tracked object instead of
    # querying for a row that has not been flushed yet.
    db.flush()
    instance_id = inst.id
    instance_name = inst.name
    seed_path = str(Path(root_pool.mount_path) / f"{req.name}-seed.iso")
    root_pool.allocated_size_gb += req.root_disk_gb
    root_pool.available_size_gb -= req.root_disk_gb
    if req.extra_disk_gb:
        extra_pool.allocated_size_gb += req.extra_disk_gb
        extra_pool.available_size_gb -= req.extra_disk_gb
    db.commit()
    network_configs = []
    for (net, ip_row, is_default), mac in zip(selected, macs):
        prefix = net.cidr.split("/")[1]
        network_configs.append({"mac": mac, "address": f"{ip_row.ip_address}/{prefix}",
                                "gateway": net.gateway_ip, "dns": net.dns_server, "default": bool(is_default)})
    args = (instance_id, operation_uuid, instance_name, image.file_path, root_path,
            req.root_disk_gb, extra_path, req.extra_disk_gb, req.memory_gb, req.vcores,
            [net.bridge_name for net, _, _ in selected], macs, network_configs, seed_path,
            req.guest_username, req.guest_password, req.effective_hostname,
            shared_mounts, "nfs-common" if image.linux_type.lower().startswith("ubuntu") else "nfs-utils")
    try:
        threading.Thread(target=_create_worker, args=args, daemon=True,
                         name=f"vm-create-{instance_id}").start()
    except Exception as exc:
        db.rollback()
        finalize_failed_creation(db, instance_id, operation_uuid, f"작업 시작 실패: {exc}")
        raise HTTPException(status_code=503, detail="생성 작업을 시작하지 못했습니다. 실패 이력을 확인하세요.")
    return {"id": instance_id, "operation_uuid": operation_uuid, "name": instance_name,
            "state": "CREATING", "message": "VM 생성 작업이 등록되었습니다."}

@app.post("/api/v1/instances/{instance_id}/action")
def instance_action(instance_id: int, action: str, force: bool = False, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().first()
    if not inst:
        raise HTTPException(status_code=404, detail="인스턴스를 찾을 수 없습니다.")
    if action not in {"start", "stop", "delete"}:
        raise HTTPException(status_code=400, detail="지원하지 않는 인스턴스 작업입니다.")

    target_by_action = {"start": "STARTING", "stop": "STOPPING", "delete": "DELETING"}
    domain_exists = libvirt_service.domain_exists(inst.name)
    try:
        if action == "delete" and inst.state == "ERROR" and not domain_exists:
            actual_state = "ERROR"
        else:
            actual_state = libvirt_service.domain_state(inst.name)
        inst.state = actual_state
        if action == "delete" and actual_state == "RUNNING":
            ensure_transition(actual_state, "STOPPING")
        else:
            ensure_transition(actual_state, target_by_action[action])
    except InvalidStateTransition as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except LibvirtError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"libvirt 도메인 조회 실패: {exc}")

    op = begin_operation(db, inst, action, target_by_action[action])
    operation_uuid = op.operation_uuid
    instance_name = inst.name
    # Publish slow lifecycle transitions before waiting on libvirt so every
    # operator sees progress and duplicate actions are rejected by state.
    if action in {"start", "stop"} or (action == "delete" and actual_state == "RUNNING"):
        inst.state = "STARTING" if action == "start" else "STOPPING"
        inst.error_message = None
        db.commit()
    try:
        if action == "start":
            libvirt_service.start(instance_name)
            assigned_ips = [row[0] for row in db.query(IpPool.ip_address).join(
                InstanceNic, InstanceNic.ip_pool_id == IpPool.id
            ).filter(InstanceNic.instance_id == instance_id).all()]
            vm_provisioner.wait_for_guest_network(
                instance_name, [{"address": address} for address in assigned_ips]
            )
            inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
            op = db.query(ResourceOperation).filter(
                ResourceOperation.operation_uuid == operation_uuid
            ).with_for_update().one()
            inst.state = libvirt_service.domain_state(instance_name)
            if inst.state != "RUNNING":
                raise LibvirtError(f"시작 후 예상하지 않은 상태입니다: {inst.state}")
            inst.last_desired_state = "RUNNING"
        elif action == "stop":
            libvirt_service.shutdown_and_wait(instance_name, timeout=60, force=force)
            inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
            op = db.query(ResourceOperation).filter(
                ResourceOperation.operation_uuid == operation_uuid
            ).with_for_update().one()
            inst.state = "STOPPED"
            inst.last_desired_state = "STOPPED"
        else:
            if actual_state == "RUNNING":
                libvirt_service.shutdown_and_wait(instance_name, timeout=60, force=force)
                actual_state = "STOPPED"
                inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
                op = db.query(ResourceOperation).filter(
                    ResourceOperation.operation_uuid == operation_uuid
                ).with_for_update().one()
            ensure_transition(actual_state, "DELETING")
            inst.state = "DELETING"
            db.commit()
            inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
            updater = configured_ddns()
            if updater and inst.private_ip:
                updater.remove(inst.dns_hostname, inst.private_ip)
            op = db.query(ResourceOperation).filter(
                ResourceOperation.operation_uuid == operation_uuid
            ).with_for_update().one()
            disks = db.query(VirtualDisk).filter(VirtualDisk.instance_id == inst.id).all()
            if domain_exists:
                libvirt_service.undefine(inst.name)
            for disk in disks:
                remove_managed_disk_file(disk.file_path)
                pool = db.query(StoragePool).filter(StoragePool.id == disk.pool_id).with_for_update().first()
                if pool:
                    pool.allocated_size_gb = max(0, float(pool.allocated_size_gb) - disk.size_gb)
                    pool.available_size_gb = min(float(pool.total_size_gb), float(pool.available_size_gb) + disk.size_gb)
                disk.status = "DELETED"
                disk.is_assigned = 0
            seed_path = storage_path("root_disk_dir") / f"{inst.name}-seed.iso"
            if seed_path.exists():
                seed_path.unlink()
            for ip_row in db.query(IpPool).filter(IpPool.instance_id == inst.id).all():
                ip_row.status = "AVAILABLE"
                ip_row.instance_id = None
                ip_row.assigned_at = None
                net = db.query(VirtualNetwork).filter(VirtualNetwork.id == ip_row.network_id).first()
                if net and net.assigned_ips > 0:
                    net.assigned_ips -= 1
            db.query(InstanceNic).filter(InstanceNic.instance_id == inst.id).delete(synchronize_session=False)
            inst.state = "DELETED"
        finish_operation(op)
        db.commit()
        return {"status": "ok", "state": inst.state, "operation_uuid": op.operation_uuid}
    except (LibvirtError, ProvisioningError, InvalidStateTransition, OSError, RuntimeError) as exc:
        db.rollback()
        inst = db.query(Instance).filter(Instance.id == instance_id).with_for_update().one()
        op = db.query(ResourceOperation).filter(
            ResourceOperation.operation_uuid == operation_uuid
        ).with_for_update().one()
        try:
            inst.state = libvirt_service.domain_state(instance_name)
        except LibvirtError:
            inst.state = "ERROR"
        finish_operation(op, str(exc))
        inst.error_message = str(exc)
        db.commit()
        status_code = 409 if isinstance(exc, InvalidStateTransition) else 503
        raise HTTPException(status_code=status_code, detail=str(exc))

@app.post("/api/v1/instances/{instance_id}/resize")
def resize_instance(instance_id: int, req: ResizeInstanceReq, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    if not 1 <= req.vcores <= 4 or not 1 <= req.memory_gb <= 8:
        raise HTTPException(status_code=422, detail="VCore는 1~4, 메모리는 1~8GB 범위여야 합니다.")
    inst = db.query(Instance).filter(Instance.id == instance_id, Instance.state != "DELETED").with_for_update().first()
    if not inst:
        raise HTTPException(status_code=404, detail="인스턴스를 찾을 수 없습니다.")
    actual = libvirt_service.domain_state(inst.name)
    if actual != "STOPPED":
        raise HTTPException(status_code=409, detail="중지된 인스턴스만 크기를 조정할 수 있습니다.")
    op = begin_operation(db, inst, "resize", "STOPPED")
    try:
        inst.state = "RESIZING"
        libvirt_service.set_resources(inst.name, req.vcores, req.memory_gb)
        inst.vcores, inst.memory_gb, inst.state = req.vcores, req.memory_gb, "STOPPED"
        finish_operation(op)
        db.commit()
        return {"status": "ok", "operation_uuid": op.operation_uuid}
    except LibvirtError as exc:
        finish_operation(op, str(exc)); inst.state = "ERROR"; db.commit()
        raise HTTPException(status_code=503, detail=str(exc))

@app.get("/api/v1/disks")
def list_disks(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    disks = db.query(VirtualDisk).filter(VirtualDisk.status != "DELETED").order_by(VirtualDisk.id.desc()).all()
    return [
        {"id": d.id, "disk_uuid": d.disk_uuid, "disk_name": d.disk_name, "disk_role": d.disk_role,
         "size_gb": d.size_gb, "file_path": d.file_path, "file_exists": Path(d.file_path).is_file(),
         "is_assigned": bool(d.is_assigned), "instance_id": d.instance_id, "target_dev": d.target_dev, "status": d.status}
        for d in disks
    ]

@app.get("/api/v1/storage/summary")
def storage_summary(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    return synchronize_storage_pools(db)

@app.post("/api/v1/disks")
def create_disk(req: CreateDiskReq, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    if not req.disk_name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="디스크 이름은 영문·숫자·-_만 사용할 수 있습니다.")
    pool = db.query(StoragePool).filter(StoragePool.pool_type == "EXTRA").with_for_update().first()
    if not pool or float(pool.available_size_gb) < req.size_gb:
        db.rollback(); raise HTTPException(status_code=409, detail="추가 디스크 가용 용량이 부족합니다.")
    file_path = str(Path(pool.mount_path) / f"{req.disk_name}.qcow2")
    d = VirtualDisk(
        disk_uuid=str(uuid.uuid4()), disk_name=req.disk_name, pool_id=pool.id if pool else 2,
        disk_role="EXTRA", size_gb=req.size_gb, file_path=file_path,
        is_assigned=0, status="AVAILABLE"
    )
    db.add(d)
    try:
        vm_provisioner.create_empty_disk(file_path, req.size_gb)
        pool.allocated_size_gb += req.size_gb; pool.available_size_gb -= req.size_gb
        db.commit()
    except ProvisioningError as exc:
        db.rollback(); Path(file_path).unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=str(exc))
    return {"id": d.id, "disk_name": d.disk_name}

@app.post("/api/v1/disks/{disk_id}/action")
def disk_action(disk_id: int, req: DiskActionReq, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    disk = db.query(VirtualDisk).filter(VirtualDisk.id == disk_id, VirtualDisk.status != "DELETED").with_for_update().first()
    if not disk:
        raise HTTPException(status_code=404, detail="디스크를 찾을 수 없습니다.")
    if req.action == "attach":
        inst = db.query(Instance).filter(Instance.id == req.instance_id).with_for_update().first()
        if not inst or libvirt_service.domain_state(inst.name) != "STOPPED" or disk.is_assigned:
            raise HTTPException(status_code=409, detail="중지된 인스턴스와 미할당 디스크가 필요합니다.")
        libvirt_service.attach_disk(inst.name, disk.file_path, req.target_dev)
        disk.instance_id, disk.target_dev, disk.is_assigned, disk.status = inst.id, req.target_dev, 1, "IN_USE"
    elif req.action == "detach":
        inst = db.query(Instance).filter(Instance.id == disk.instance_id).first()
        if not inst or libvirt_service.domain_state(inst.name) != "STOPPED":
            raise HTTPException(status_code=409, detail="중지된 인스턴스에서만 분리할 수 있습니다.")
        libvirt_service.detach_disk(inst.name, disk.target_dev)
        disk.instance_id, disk.target_dev, disk.is_assigned, disk.status = None, None, 0, "AVAILABLE"
    elif req.action == "delete":
        if disk.is_assigned:
            raise HTTPException(status_code=409, detail="할당된 디스크는 삭제할 수 없습니다.")
        remove_managed_disk_file(disk.file_path)
        pool = db.query(StoragePool).filter(StoragePool.id == disk.pool_id).with_for_update().first()
        if pool:
            pool.allocated_size_gb = max(0, float(pool.allocated_size_gb) - disk.size_gb)
            pool.available_size_gb = min(float(pool.total_size_gb), float(pool.available_size_gb) + disk.size_gb)
        disk.status = "DELETED"
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 디스크 작업입니다.")
    db.commit()
    return {"status": "ok"}

@app.get("/api/v1/networks")
def list_networks(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    nets = db.query(VirtualNetwork).all()
    res = []
    for n in nets:
        ips = db.query(IpPool).filter(IpPool.network_id == n.id).limit(30).all()
        res.append({
            "id": n.id, "net_name": n.net_name, "net_type": n.net_type, "bridge_name": n.bridge_name,
            "cidr": n.cidr, "gateway_ip": n.gateway_ip, "ip_start": n.ip_start, "ip_end": n.ip_end,
            "total_ips": n.total_ips, "assigned_ips": n.assigned_ips,
            "remaining_ips": max(0, n.total_ips - n.assigned_ips),
            "ips": [{"ip": i.ip_address, "status": i.status, "instance_id": i.instance_id} for i in ips]
        })
    return res

@app.get("/api/v1/ip-pools")
def list_ip_pools(network_id: int = None, network_type: str = None, allocation: str = "ALL", offset: int = 0, limit: int = 100, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    if allocation not in ("ALL", "USED", "AVAILABLE"):
        raise HTTPException(status_code=422, detail="allocation은 ALL, USED, AVAILABLE 중 하나여야 합니다.")
    query = db.query(IpPool, VirtualNetwork, Instance).join(VirtualNetwork, IpPool.network_id == VirtualNetwork.id).outerjoin(Instance, IpPool.instance_id == Instance.id)
    if network_id is not None:
        query = query.filter(IpPool.network_id == network_id)
    if network_type:
        query = query.filter(VirtualNetwork.net_type == network_type)
    if allocation == "USED":
        query = query.filter(IpPool.status == "ASSIGNED")
    elif allocation == "AVAILABLE":
        query = query.filter(IpPool.status == "AVAILABLE")
    rows = query.order_by(VirtualNetwork.id, IpPool.ip_address).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    return [{"id": ip.id, "network": net.net_name, "ip": ip.ip_address, "status": ip.status,
             "instance_id": inst.id if inst else None, "instance_name": inst.name if inst else None,
             "instance_state": inst.state if inst else None, "assigned_at": ip.assigned_at} for ip, net, inst in rows]

@app.get("/api/v1/images")
def list_images(db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    imgs = db.query(OsImage).filter(OsImage.status != "DELETED").all()
    return [
        {"id": im.id, "linux_type": im.linux_type, "version": im.version, "image_format": im.image_format,
         "file_name": im.file_name, "size_mb": im.size_mb, "source_url": im.source_url,
         "checksum_sha256": im.checksum_sha256, "released_at": im.released_at,
         "registered_at": im.registered_at,
         "status": "MISSING" if im.status == "READY" and not Path(im.file_path).is_file() else im.status}
        for im in imgs
    ]

def queue_image_operation(db: Session, image_id: int, action: str) -> ResourceOperation:
    existing = db.query(ResourceOperation).filter(
        ResourceOperation.resource_type == "IMAGE",
        ResourceOperation.resource_id == image_id,
        ResourceOperation.status.in_(("PENDING", "RUNNING")),
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="이미지 작업이 이미 진행 중입니다.")
    operation = ResourceOperation(
        operation_uuid=str(uuid.uuid4()), resource_type="IMAGE", resource_id=image_id,
        action=action, status="PENDING",
    )
    db.add(operation)
    db.commit()
    image_worker.wake()
    return operation

@app.post("/api/v1/images/{image_id}/update")
def update_image(image_id: int, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    image = db.query(OsImage).filter(OsImage.id == image_id, OsImage.status != "DELETED").first()
    if not image:
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
    operation = queue_image_operation(db, image_id, "DOWNLOAD")
    return {"status": "scheduled", "operation_uuid": operation.operation_uuid}

@app.delete("/api/v1/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db), _admin: str = Depends(require_active_admin)):
    if db.query(Instance).filter(Instance.os_image_id == image_id, Instance.state != "DELETED").first():
        raise HTTPException(status_code=409, detail="사용 중인 OS 이미지는 삭제할 수 없습니다.")
    image = db.query(OsImage).filter(OsImage.id == image_id, OsImage.status != "DELETED").first()
    if not image:
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")
    operation = queue_image_operation(db, image_id, "DELETE")
    return {"status": "scheduled", "operation_uuid": operation.operation_uuid}

@app.get("/")
def serve_spa_root():
    idx = Path(settings.FRONTEND_DIST_DIR) / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"message": "Personal KVM Service v0.0.6 API Running", "docs": "/docs"}
