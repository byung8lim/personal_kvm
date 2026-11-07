# 퍼스널 KVM 가상화 서비스 v0.1.9

## 주요 기능 및 패치 사항
1. `443` HTTPS 관리 콘솔 제공, `80`/`8080`은 HTTPS로 전환, 백엔드 `8000`은 로컬 루프백에서만 수신
2. `/etc/personal-kvm/.env` 누락 원천 차단 (`config/.` 복사, 명시적 `.env` 복사, 자동 생성 폴백)
3. 4대 가상 네트워크(`virbr0`~`virbr3`) 충돌 자동 정리 및 IPAM IP 풀 시드 적재
4. 전체 웹 콘솔 UI(대시보드, 7단계 인스턴스 생성 위저드, 가상 디스크, 4대 네트워크, OS 이미지 관리) 및 FastAPI 백엔드 탑재
5. 비root `pkvm` 서비스, QEMU 자동 탐지, TLS 자동 갱신, Alembic 순차 마이그레이션, Playwright/axe 및 Rocky KVM 인수시험 자동화

## 설치 및 실행
```bash
git clone https://github.com/byung8lim/personal_kvm.git
cd personal_kvm
chmod +x ./scripts/*.sh
sudo ./scripts/install.sh
```

설치 완료 후 접속 정보와 초기 관리자 계정은 화면에 출력됩니다. **비밀번호는 터미널 로그에 출력하지 않습니다.** root 권한으로 초기 비밀번호 파일을 별도 확인하십시오.

```bash
sudo cat /etc/personal-kvm/install-summary
sudo cat /etc/personal-kvm/.initial_admin_password
sudo systemctl is-active pkvm-backend pkvm-lb-agent nginx
curl --fail --insecure https://127.0.0.1/api/health/ready
sudo bash ./scripts/diagnose.sh
```

설치가 중단되어도 초기 암호는 root 전용 파일에 보존되고, 실패한 서비스의 상태와 최근 journal이 자동 출력됩니다. 원인을 수정한 뒤 같은 `install.sh`를 다시 실행할 수 있습니다.

## v0.1.9 감사 결함 보완

- LB Agent를 기본 설치/기동 대상으로 등록하고 Unix 소켓 존재까지 검증합니다. L4/HAProxy 및 L7/Traefik 데이터평면은 별도 관리자의 명시적 설치/구성이 필요합니다.
- HAProxy Runtime API에서 실제 백엔드 상태와 활성 연결 수를 조회하며, 기존 연결이 남았을 때 수동 복귀를 완료 처리하지 않고 시간 초과 시 Standby로 복구합니다.
- DNS A/PTR은 기대 DB 값과 실제 BIND9 질의 응답을 구분해 보여줍니다(질의를 실행한 서버의 DNS View만 확인).
- Traefik 4종 L7 분산 방식은 **3.4 이상**에서만 활성화할 수 있도록 설치 및 배포 시 검사합니다.
- `scripts/diagnose.sh`는 호스트 상태를 변경하지 않고 서비스/포트/구성 상태를 진단합니다.
- **중요:** 실제 Rocky 9.8/10.x 호스트·KVM·BIND9·HAProxy·Traefik 통합 시험을 대신하는 것은 아닙니다. 설치 후 `docs/V0.1.9_SERVER_DEBUG_GUIDE.md`에 따라 확인해야 합니다.
