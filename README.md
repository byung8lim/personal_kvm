# 퍼스널 KVM 가상화 서비스 v0.0.8

## 주요 기능 및 패치 사항
1. `443` HTTPS 관리 콘솔 제공, `80`/`8080`은 HTTPS로 전환, 백엔드 `8000`은 로컬 루프백에서만 수신
2. `/etc/personal-kvm/.env` 누락 원천 차단 (`config/.` 복사, 명시적 `.env` 복사, 자동 생성 폴백)
3. 4대 가상 네트워크(`virbr0`~`virbr3`) 충돌 자동 정리 및 IPAM IP 풀 시드 적재
4. 전체 웹 콘솔 UI(대시보드, 7단계 인스턴스 생성 위저드, 가상 디스크, 4대 네트워크, OS 이미지 관리) 및 FastAPI 백엔드 탑재
5. 비root `pkvm` 서비스, QEMU 자동 탐지, TLS 자동 갱신, Alembic 순차 마이그레이션, Playwright/axe 및 Rocky KVM 인수시험 자동화

## 설치 및 실행
```bash
unzip personal-kvm-service-v0.0.7.zip
cd personal-kvm-service-v0.0.7
chmod ./scripts/*.sh
sudo ./scripts/install.sh
```

설치 완료 후 접속 정보와 초기 관리자 계정은 화면에 출력되며, 다음 명령으로도 다시 확인할 수 있다.

```bash
sudo cat /etc/personal-kvm/install-summary
sudo cat /etc/personal-kvm/.initial_admin_password
sudo systemctl is-active pkvm-backend nginx libvirtd mysqld
curl --fail --insecure https://127.0.0.1/api/health/ready
```

설치가 중단되어도 초기 암호는 root 전용 파일에 보존되고, 실패한 서비스의 상태와 최근 journal이 자동 출력된다. 원인을 수정한 뒤 같은 `install.sh`를 다시 실행할 수 있다.
