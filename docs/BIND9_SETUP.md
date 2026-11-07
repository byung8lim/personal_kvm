# Bind9 설치 및 연동 절차 (v0.1.x 개발 단계)

## 선행 조건
Rocky Linux 9.x/10.x, `pkvm` 계정 생성 완료, root 권한, DNS 서버로 사용할 호스트 IP와 내부 DNS 영역 결정.
운영 중인 DNS 영역을 중복 생성하지 않도록 먼저 `named.conf`와 기존 zone 파일을 점검하세요.

## 설치 (선택 기능)
```bash
sudo bash scripts/setup_bind9.sh internal.example 60.10.10.in-addr.arpa 10.10.60.1
sudo named-checkconf -z
sudo systemctl status named --no-pager
```

TSIG 비밀 키는 호스트의 `/etc/named/pkvm/pkvm-ddns.key`에만 생성하며 GitHub에 올리지 않습니다.
기존 `/etc/named.conf`는 수정 전 타임스탬프 백업을 생성합니다. 설정이 유효하지 않으면 백업에서 수동 복구해야 합니다.

## personal_kvm 설정
서버가 DNS 응답 및 동적 갱신을 수행하는지 확인한 뒤, 배포된 `/etc/personal-kvm/config.yaml`의 `dns` 값을 수정하세요.

```yaml
dns:
  enabled: true
  server: 10.10.60.1
  key_file: /etc/named/pkvm/pkvm-ddns.key
  forward_zone: internal.example
  reverse_zone: 60.10.10.in-addr.arpa
  ttl: 300
```

수정 후 `pkvm-backend.service`를 재시작하여 기존 프로세스의 `named` 그룹 권한을 적용합니다. 파일과 상위 디렉터리의 접근 권한도 확인하세요.
`nsupdate -k`와 `dig @10.10.60.1` 명령으로 A/PTR 등록·조회·삭제를 검증합니다.

## 주의 및 남은 작업
이 스크립트는 DNS zone을 초기화하는 도구이며 기존 named의 `listen-on`, `allow-query`, SELinux, firewalld를 자동 변경하지 않습니다. 외부망에 DNS를 공개하지 말고 내부망만 허용하세요. `install.sh` 자동 호출, 설치 오류 자동 롤백 및 실제 Rocky 9/10 통합 테스트는 별도 작업입니다.


## 기존 통합 설치기에 연결
`scripts/install.sh` 실행 전에 다음 환경변수를 지정하면 Bind9 설치를 선택적으로 활성화합니다.
```bash
sudo env \
  PKVM_SETUP_BIND9=1 \
  PKVM_DNS_FORWARD_ZONE=internal.example \
  PKVM_DNS_REVERSE_ZONE=60.10.10.in-addr.arpa \
  PKVM_DNS_SERVER_IP=10.10.60.1 \
  bash scripts/install.sh
```
설치 시 `/etc/personal-kvm/config.yaml`의 `dns` 섹션을 활성화하며, 파일은 `root:pkvm` 소유 및 `0640` 권한으로 저장합니다.
이 옵션을 지정하지 않으면 기존 설치기가 Bind9를 자동 구성하지 않습니다.
정적 회귀 점검: `bash scripts/test_bind9_install.sh`. 이 점검은 실제 DNS 응답을 검증하지 않습니다.

### 아직 검증이 필요한 항목
- Rocky 9.8/10.x 클린 OS에서 실제 패키지 설치와 named 서비스 기동
- SELinux Enforcing 모드에서 영역 파일 동적 갱신 권한
- DNS 리스닝 주소/내부 방화벽 규칙, 캐시 및 재시작 후 지속성
- VM 생성/삭제와 DDNS A/PTR 정방향·역방향 실제 조회
- 재설치, 오작동 복구, 보상 작업의 통합 테스트
