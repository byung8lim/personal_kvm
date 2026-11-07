# Rocky Linux 9.x / 10.x 설치 호환성 안내

이 버전은 호스트 OS의 메이저 버전을 감지하고 패키지/DB/systemd를 확인하여 분기합니다. 사용자가 제공한 Rocky Linux 10.2의 패키지 실측 목록을 반영했지만 **Rocky 9 및 Rocky 10에서 실설치/재부팅 검증은 수행하지 않았습니다**.

`scripts/init.sh`는 이제 `scripts/install.sh`로 위임하므로 호스트명, SELinux, firewalld를 무조건 변경하지 않습니다. 이미지 맞춤화 스크립트도 게스트의 cloud-init을 제거하지 않습니다.

## 안전한 설치 전제

- `sudo bash scripts/install.sh`를 실행하기 전 별도 콘솔/IPMI 접근을 확보하십시오.
- 물리 NIC를 libvirt 외부 브리지로 이동할 때 `nmcli device checkpoint`가 확인을 요청합니다. 승인하지 않으면 자동 롤백되며 비대화형 설치에서는 중단합니다.
- 실제 호스트 IP/NIC/GW는 설치 시 `ip` 명령으로 확인되며 `PKVM_PHYS_NIC`, `PKVM_HOST_BR_IP`, `PKVM_HOST_BR_GW`로 오버라이드할 수 있습니다. 예시 설정 `config/config.yaml`은 사용자의 Rocky 10.2 호스트 값입니다.
- 설치 전 활성 DNF 저장소에서 패키지 제공을 확인하십시오. Rocky 9의 DB는 기존 서버 또는 실제 제공 패키지를 선택하며 미확인 패키지를 추정 설치하지 않습니다.
- 기본 guest Rocky 9.8 이미지는 호스트 OS와 별개입니다. `PKVM_DOWNLOAD_DEFAULT_IMAGE=0`로 설치하면 이미지 다운로드를 건너뛰며 나중에 등록할 수 있습니다.
- `frontend/`는 프로젝트 소스로부터 `npm ci`/`npm run build`합니다. Node.js 20.19+ 또는 22.12+가 필요합니다.

## OS 분기

| 기능 | Rocky 9.x | Rocky 10.x |
|---|---|---|
| Python | 시스템 python3 (최소 3.9), wheel 설치 | 시스템 python3 (실측 3.12.13), wheel 설치 |
| libguestfs | `guestfs-tools` / `libguestfs-tools` 실제 제공 확인 | `guestfs-tools` 실측 제공 |
| ISO | 설치된 `cloud-localds` / `genisoimage` / `xorriso` 탐색 | `xorriso` 실측 제공 |
| DB | 기존 MySQL/MariaDB 유지, 없으면 제공 패키지 확인 | MariaDB 우선, 서비스 유닛 실재 검사 |
| libvirt | 설치된 단일/분리형 유닛 검사 | 설치된 단일/분리형 유닛 검사 |
| NIC 브리지 | NetworkManager checkpoint + 확인 | NetworkManager checkpoint + 확인 |

## 테스트

호스트를 변경하지 않는 검사: `bash -n scripts/*.sh` (스크립트별 순회), `python3 -m compileall -q backend/app backend/tests`. 백엔드 의존성을 설치한 검증 환경에서는 `PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v`.

## 확인되지 않은 사항

운영 호스트의 SELinux/firewalld 실제 정책, 각 배포판의 전체 패키지 제공, 실제 libguestfs 어플라이언스 생성, VM 게스트 IP 접근과 SSH 유지/롤백은 설치 전 별도 테스트가 필요합니다.


## v0.0.9: virt-customize 임시 디렉터리 쓰기 오류 수정

- `pkvm-backend.service`: `ProtectSystem=strict` 유지, `/tmp` 및 `/var/tmp` 쓰기 허용, `PrivateTmp=true`로 서비스 전용 임시 디렉터리 구성.
- `customize_guest.sh`, `customize_rocky.sh`, `customize_ubuntu.sh`: libguestfs 임시/캐시 경로 명시 및 접근 가능 여부 검사.
- `install.sh`, `acceptance.sh`: 실제 백엔드 mount namespace에서 `pkvm` 사용자로 임시 폴더 생성/삭제 검사. 실패 시 원인을 출력하고 설치 실패 처리.
- **기존 v0.0.8 설치를 업그레이드하는 경우**: 전체 소스를 배포한 뒤 `/etc/systemd/system/pkvm-backend.service`도 새로운 유닛 파일로 갱신하고 `systemctl daemon-reload && systemctl restart pkvm-backend.service` 수행. 소스 디렉터리의 유닛만 교체해서는 실행 중인 유닛 설정이 변경되지 않음.
- 실제 Rocky Linux 9.x/10.x 서버에서 VM 생성 성공은 별도 운영 검증 필요.
