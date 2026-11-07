# Personal KVM Service (v0.2.0)

## 1. Personal KVM Service(v0.2.0) 개요

Personal KVM Service는 **한 대의 Linux 서버에서 여러 가상머신(VM)을 생성·운영하는 웹 기반 관리 서비스**입니다. VM은 독립적인 운영체제로 실행되는 가상 서버를 의미합니다. 관리자는 웹 콘솔에서 CPU·메모리·디스크·네트워크를 할당하고, VM 및 관련 서비스를 관리할 수 있습니다.

- **가상화 기반:** KVM/QEMU, libvirt, qcow2 디스크, cloud-init을 통한 게스트 초기 설정
- **관리 서비스:** React 웹 콘솔, FastAPI 관리 API, MariaDB/MySQL 메타데이터 및 작업 이력
- **기본 구성:** VM·디스크·네트워크/IP 주소·OS 이미지 관리
- **선택 구성:** BIND9 DNS, NFS 공유볼륨, HAProxy L4 및 Traefik L7 로드밸런서
- **지원 대상:** Rocky Linux 9.x 및 10.x 호스트를 대상으로 설치 절차 구성
- **보안:** 관리자 인증, HTTPS 관리 화면, 로컬 전용 백엔드 API, 보호된 LB Agent 소켓

> **버전 안내:** 이 README는 요청한 **v0.2.0 서비스 소개 체계**로 작성했습니다. 2026-11-07 기준 GitHub `main`에 반영된 **실제 소스 버전은 v0.1.9**입니다. v0.2.0 소프트웨어 릴리스 또는 Rocky 양 버전의 실환경 검증이 완료되었다는 의미는 아닙니다.

## 2. 주요 서비스

### 2.1 VM 인스턴스 관리

웹 콘솔에서 7단계 생성 화면을 통해 VM을 구성하고 관리합니다.

| 기능 | 설명 |
|---|---|
| VM 생성 | 준비 상태(`READY`)의 OS 이미지 선택, VM 이름과 게스트 관리자 계정 설정, CPU·메모리·네트워크·볼륨 할당 |
| 사양 설정 | VM당 vCPU **1~4개**, 메모리 **1~8GB**, 루트 디스크 **10~100GB** 설정(서버 입력 검증 기준) |
| 추가 디스크 | 생성 시 추가 디스크 **0GB(없음) 또는 10~100GB** 선택. 별도 디스크 관리 기능으로 볼륨 연결 가능 |
| 초기 OS 설정 | cloud-init을 사용하여 게스트 계정, 초기 암호, 네트워크 주소 등 구성 |
| 실행 관리 | VM 시작·정지·강제 정지·삭제 및 **정지 상태에서** CPU/메모리 크기 조정 |
| 생성 진행 상태 | 요청별 작업 ID, 생성 단계, 성공·실패와 오류 내용을 조회하여 문제 분석 지원 |
| 자원 회수 | 삭제 시 관리 대상 도메인·디스크·IP 할당 등 회수 처리. 실제 회수 여부는 호스트에서 확인 필요 |

VM 생성은 백그라운드 작업으로 처리되므로 완료 전에는 작업 현황에서 처리 단계를 확인합니다. 사용 가능한 자원과 디스크 공간에 따라 요청이 거부될 수 있습니다.

### 2.2 디스크 관리

- 호스트 스토리지의 **전체·할당·가용 용량**, 가상 디스크 수와 연결 상태 조회
- `qcow2` 형식의 독립 가상 디스크(10~100GB) 생성
- 생성한 디스크를 **중지된 VM**에 연결(Attach)하거나 분리(Detach)
- 사용 중인 디스크 삭제 방지, 미할당 디스크 삭제 및 스토리지 할당량 갱신
- 디스크 파일 경로·존재 여부·용량·할당 VM을 목록에서 확인

스토리지 경로는 기본적으로 OS 이미지 `/data/images`, VM 루트 디스크 `/data/instances`, 추가 디스크 `/data2/disks`를 사용하며 설치 환경에 맞춰 확인해야 합니다.

### 2.3 네트워크 서비스

VM에 연결할 수 있는 네트워크는 다음 네 종류입니다.

| 네트워크 | 기본 브리지 | 용도 |
|---|---|---|
| Public-Bridged | `virbr0` | 물리 네트워크와 연결해 외부 호스트에서 VM으로 접근하는 용도 |
| Public-NAT | `virbr1` | 주소 변환(NAT)을 통해 VM의 외부 통신 지원 |
| Internal-Service | `virbr2` | 외부와 분리된 VM 간 내부 서비스 통신 |
| Management-Net | `virbr3` | 관리·공유 스토리지 등 내부 운영 통신을 위한 선택적 네트워크 |

- **IPAM(IP 주소 관리):** 네트워크별 CIDR·게이트웨이·할당 가능 IP 범위, 사용·미사용 IP 및 소유 VM 조회
- **IP 할당 방식:** 기본 `RANDOM`, 설정에 따라 `SERIAL` 방식 사용 가능
- **VM 네트워크 설정:** 선택한 가상 NIC·IP·MAC을 관리하고 cloud-init으로 게스트에 반영
- **선택형 DNS:** BIND9와 TSIG 키를 별도로 구성하면 VM 소유 A/PTR 레코드 등록·삭제, 상태 조회 및 정합성 확인 지원

물리 NIC를 브리지에 연결하는 작업은 호스트의 SSH 연결에 영향을 줄 수 있습니다. **설치 전에 로컬 콘솔 또는 원격 관리 콘솔을 확보**하십시오. DNS의 분리된 View는 실제 각 네트워크의 클라이언트에서 별도로 검증해야 합니다.

### 2.4 공유볼륨 서비스

NFS는 여러 VM이 네트워크를 통해 같은 디렉터리에 접근하는 공유 파일 서비스입니다.

- 관리자가 지정한 전용 `NFS_DIR` 아래 공유볼륨 생성·조회·삭제
- 여러 VM에서 동일한 공유볼륨 사용 가능하도록 마운트 정보 관리
- 관리 네트워크(Management-Net)를 사용하는 VM에서 공유볼륨 연결
- 연결 중인 VM이 있는 공유볼륨은 삭제를 차단하고, 관리 디렉터리 외부 경로 접근을 제한

**NFS는 별도 설정이 필요합니다.** 호스트에 NFS 서비스를 준비하고 관리 API가 읽는 설정에 `nfs.NFS_DIR`을 지정해야 합니다. 또한 **연결된 VM이 없더라도 볼륨 삭제는 내부 파일을 제거할 수 있으므로**, 필요한 데이터는 먼저 백업하십시오.

### 2.5 로드밸런스 서비스

로드밸런서는 여러 백엔드 서버에 요청을 분산하거나 장애 서버로의 연결을 줄이는 기능입니다. **기본 서비스 설치와 별도로 HAProxy/Traefik 데이터평면을 준비·활성화**해야 합니다.

| 구분 | 구성 요소 | 제공 기능 |
|---|---|---|
| L4 (TCP) | HAProxy | IP:포트 기반 TCP 전달, `roundrobin`·`leastconn`·`source` 알고리즘, 백엔드 연결 상태 확인 |
| L4 백엔드 HA | HAProxy + 상태 감시기 | Primary/Standby 장애 전환, 관리자 수동 Failback, 기존 TCP 연결의 Drain 대기·시간 초과 시 복구 시도 |
| L7 (HTTP/HTTPS) | Traefik | 도메인(Host)·경로(PathPrefix) 기준 라우팅, `wrr`·`p2c`·`hrw`·`leasttime` 분산 전략, HTTP 헬스체크 설정 |

- 관리 API/UI에서 서비스 정의를 생성하고 설정 적용·상태 조회 수행
- L4는 보호된 UNIX 소켓을 통해 **실제 HAProxy 백엔드 상태와 현재 연결 수**를 조회
- L7의 네 가지 알고리즘은 **Traefik 3.4 이상**이 필요하며 실제 트래픽으로 동작 검증해야 함
- Failback은 기존 연결이 종료될 때까지 기다리며, 제한 시간 내 종료되지 않으면 Standby 유지로 복구 시도

현재 L7의 실시간 백엔드 건강 상태는 관리 API에서 별도로 검증되지 않았으므로, 실제 요청·로그로 확인해야 합니다.

### 2.6 OS 이미지

- VM 생성에 사용할 OS 이미지 목록, 배포판·버전·파일 형식·크기·상태 조회
- 이미지 다운로드/갱신 작업 요청 및 작업 상태 기록
- 이미지 파일 존재 여부 및 저장된 SHA-256 체크섬 검증을 통한 생성 입력 확인
- 사용 중인 이미지 삭제 제한 및 이미지 삭제 작업 처리
- Rocky 등 등록된 Cloud 이미지에서 VM 생성에 필요한 파일을 준비

기본 OS 이미지의 다운로드 여부는 설치 옵션에 따라 달라집니다. 이미지가 없거나 `READY`가 아니면 VM 생성 전에 이미지 상태를 확인해야 합니다.

## 3. Personal KVM Service 설치 가이드

### 3.1 설치 전 준비

1. **전용 시험 서버**에서 Rocky Linux 9.x 또는 10.x, BIOS/UEFI의 가상화 기능(VT-x/AMD-V), 인터넷/패키지 저장소 및 사용 가능한 디스크를 확인합니다.
2. 물리 NIC·호스트 IP·게이트웨이와 기존 libvirt VM, 네트워크, DB, nginx, 스토리지의 사용 여부를 조사합니다.
3. NIC 브리지 전환 중 원격 접속이 끊길 경우 복구할 **콘솔/IPMI 접속 수단과 백업**을 마련합니다.
4. 대상 OS의 실제 설치 결과는 아직 별도 인수 검증이 필요합니다. 운영 서버에 즉시 적용하지 마십시오.

설치 스크립트는 호스트 환경에 따라 패키지와 서비스를 확인하지만 네트워크·DB·nginx·방화벽 등 시스템 설정을 변경합니다. [Rocky 9/10 호환성 주의사항](ROCKY_9_10_SUPPORT.md)을 먼저 읽으십시오.

### 3.2 소스 내려받기 및 사전 검사

```bash
git clone https://github.com/byung8lim/personal_kvm.git
cd personal_kvm

git log -1 --oneline
sudo bash scripts/preflight_check.sh
```

`main` 브랜치는 개발이 계속되는 코드입니다. 시험 결과를 재현하려면 설치 당시의 **커밋 SHA**를 기록하십시오.

### 3.3 기본 서비스 설치

```bash
sudo bash scripts/install.sh
```

SSH 세션을 유지하지 않고 전체 설치를 자동 실행하려면 NetworkManager 브리지 전환을 명시적으로 승인해야 합니다. 콘솔/IPMI 복구 수단을 준비한 뒤 다음과 같이 실행합니다.

```bash
mkdir -p logs
nohup sudo env PKVM_ALLOW_NONINTERACTIVE_NETWORK=1 bash scripts/install.sh \
  >logs/install.log 2>&1 </dev/null &
```

이 환경변수는 물리 NIC를 브리지로 전환하고 NetworkManager 체크포인트 검증 성공 후 변경을 자동 확정합니다. 지정하지 않은 비대화형 실행은 네트워크 단계에서 안전하게 중단됩니다.

설치 과정에서는 의존 패키지, 가상 네트워크, DB, 백엔드, 프런트엔드 빌드 및 systemd 서비스를 구성합니다. 성공 후 관리 콘솔은 기본적으로 HTTPS `443`을 사용하고, HTTP `80`/`8080`은 HTTPS로 전환되며 관리 API `8000`은 호스트 루프백에서만 수신하도록 구성합니다.

초기 관리자 비밀번호는 터미널 로그에 표시하지 않고 root 전용 파일에 보관합니다.

```bash
sudo cat /etc/personal-kvm/install-summary
sudo cat /etc/personal-kvm/.initial_admin_password
```

### 3.4 선택 서비스 구성

- **BIND9 DNS:** 설치 시 `PKVM_SETUP_BIND9=1`과 필수 Zone/IP 환경변수를 명시하면 설정 절차를 실행합니다. 자세한 내용은 [BIND9 설정 가이드](docs/BIND9_SETUP.md)를 참조하십시오.
- **L4 HAProxy:** 별도 패키지/서비스 설정 후 `sudo bash /opt/personal-kvm/scripts/setup_lb.sh haproxy 8404`를 수행하고 웹 콘솔에서 리스너와 백엔드를 정의·적용합니다. 실제 리스너 포트는 서비스 정의에서 지정합니다.
- **L7 Traefik:** `traefik version`으로 **3.4 이상**을 확인한 후 `sudo bash /opt/personal-kvm/scripts/setup_lb.sh traefik 8443`을 실행합니다. Traefik 실행 파일은 사전에 준비해야 합니다.
- **NFS 공유볼륨:** `sudo bash /opt/personal-kvm/scripts/setup_nfs.sh <NFS_DIR> <MANAGEMENT_CIDR>`로 별도 NFS 구성을 수행하고 `/etc/personal-kvm/config.yaml`의 `nfs.NFS_DIR`을 지정합니다. 변경된 설정은 백엔드 재시작 후 확인합니다.

위 명령의 네트워크/Zone/경로는 **예시가 아닌 실제 서버 환경에 맞게 결정**해야 합니다. 기존 운영 서비스와 충돌하지 않는지 먼저 확인하십시오.

### 3.5 설치 결과 확인 및 장애 진단

```bash
sudo systemctl is-active pkvm-backend pkvm-lb-agent nginx
curl -kfsS https://127.0.0.1/api/health/ready
sudo bash scripts/diagnose.sh
```

설치 중 오류가 발생하면 실패 단계와 서비스 로그를 확인합니다.

```bash
sudo cat /var/lib/personal-kvm/install-state/failed-stage
sudo journalctl -u pkvm-backend -n 100 --no-pager
sudo journalctl -u pkvm-lb-agent -n 100 --no-pager
```

로그를 공유할 때는 비밀번호·인증 토큰·내부 IP 등 민감한 정보를 제거하십시오. 원인을 수정한 뒤 재실행할 수 있지만 **기존 VM과 데이터가 있는 서버에서는 재설치·제거 전에 반드시 영향도를 확인**해야 합니다.

### 3.6 기능 검증과 상세 문서

- [v0.1.9 서버 설치·디버깅 가이드](docs/V0.1.9_SERVER_DEBUG_GUIDE.md)
- [v0.1.9 구현 및 검증 보고서](docs/V0.1.9_구현_및_검증_보고서.md)
- [Gemini v0.1.8 구현 감사보고서](docs/gemini_구현_감사보고서.md)

개발 환경의 코드 검사·단위 테스트와 **실제 Rocky 서버의 VM 생성·NIC 접속·DNS·L4/L7 트래픽 시험은 다른 검증 단계**입니다. 최종 운영 전에는 서버별 시험 결과를 확인해야 합니다.
