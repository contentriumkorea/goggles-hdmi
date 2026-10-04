# Goggles HDMI

DJI Goggles 3 / Avata 2의 라이브뷰를 USB로 받아 색보정·안정화 후 외부 화면에 출력하는 콘텐츠리움 프로그램입니다.

## Windows

Windows 10/11 x64용 최신 **1.2.0**은 연결 오류 코드와 문제 정보 복사 기능을 제공합니다. 기존 **1.1.0**도 계속 다운로드할 수 있습니다.

[Windows 1.2.0 설치 파일](https://github.com/contentriumkorea/goggles-hdmi/releases/download/v1.2.0/Goggles-HDMI-Setup-1.2.0.exe) · [Windows 최신 릴리스](https://github.com/contentriumkorea/goggles-hdmi/releases/latest) · [기존 Windows 1.1.0 릴리스](https://github.com/contentriumkorea/goggles-hdmi/releases/tag/v1.1.0)

기존 Windows 앱의 서명된 `releases/latest/download/release.json` 업데이트 경로는 Windows 설치 파일만 제공합니다. Mac 설치 파일은 이 경로를 사용하지 않습니다.

앱 상단의 **문제 정보 복사** 버튼은 연결 전이나 재연결 중에도 사용할 수 있습니다. 버튼을 누른 뒤 지원 채팅에 붙여넣으면 `GH-*` 오류 코드, 현재 연결 단계, 앱·운영체제 버전, 수신 상태와 최근 오류를 전달할 수 있습니다. 정상 복구 후에도 최근 오류 기록은 남습니다. 이미 수집된 상태만 사용하며 장치를 다시 검사하거나 인터넷으로 전송하지 않습니다. 비밀번호, 기기 일련번호, 개인 파일 경로, 원본 로그와 영상은 포함하지 않습니다.

## Apple Silicon macOS

**arm64 1.2.3**은 **macOS 15.6 이상**을 사용하는 호환 Apple Silicon Mac을 대상으로 합니다. M1·M2·M3·M4 및 이후 호환 M 시리즈에 공통 설치 파일 하나를 사용합니다. 모든 Mac 모델을 실기기로 검증했다는 의미는 아닙니다. Intel Mac, Windows ARM, Linux는 이 릴리스에 포함되지 않습니다.

[Apple Silicon 릴리스](https://github.com/contentriumkorea/goggles-hdmi/releases/tag/macos-arm64-v1.2.3) · [설치·실기기 검증 안내](docs/macos-release-notes.md)

**1.2.3은 남은 반복 끊김을 확인하기 위한 진단 빌드입니다.** 프레이밍 오류의 헤더 종류·길이, 미완성 헤더 접두부의 제한된 숫자값, USB 읽기 수치와 빌드 식별값을 문제 정보 복사에 추가했습니다. 원본 영상·바이트 덤프는 포함하지 않습니다. 연결 정책은 유지하며 실제 발생 원인과 개선 여부는 다음 Mac 보고서로 확인해야 합니다.

**1.2.2는 HTTPS 업데이트 인증서 오류를 수정하고 1.2.1의 USB 분할 수신 수정을 포함합니다.** `CERTIFICATE_VERIFY_FAILED`가 뜨는 이전 Mac 설치본은 브라우저로 새 pkg를 내려받아 한 번 수동 설치하세요. 인증서 검증을 끄지 않으며 프로그램에 포함된 신뢰 저장소로 업데이트 정보와 설치 파일을 검증합니다. 패키징된 앱의 실제 GitHub HTTPS 연결도 CI에서 확인합니다.

**1.2.1 수정 후 실제 Mac의 끊김 개선 여부를 재확인해야 합니다.** USB 읽기가 패킷 중간에서 나뉠 때 불필요하게 재연결하던 처리 결함을 수정하고 진단 수치를 추가했습니다. 네이티브 CI는 ARM 의존성, 패키지 실행·영상 처리·분할 패킷 처리·Cocoa UI·클립보드를 검사합니다. 앱은 임시 ad-hoc 서명이며 설치 pkg에는 Apple Developer ID 서명·공증이 없습니다. macOS의 설치·실행 확인이 필요할 수 있습니다.

고글에서 OTG 컴퓨터 유선 연결과 라이브뷰 공유를 켜고 USB 데이터 케이블로 Mac에 직접 연결하세요. 다른 DJI 장치 프로그램은 종료하세요. Mac USB 백엔드는 libusb RNDIS를 사용하며 드라이버를 강제로 분리하거나 장치를 자동 초기화하지 않습니다. 앱 활성 상태에서 Cmd+D / Ctrl+D / Escape 또는 출력 종료 버튼으로 전체화면 출력을 해제합니다. 인증 저장은 macOS 키체인을 사용합니다. Mac 업데이트는 별도 서명 채널에서 검증 후 Apple 설치 프로그램을 열며 운영체제 확인을 거칩니다.

## 소스와 빌드

선별된 앱 소스, 공개 비밀번호 검증값·업데이트 검증키, 리소스, 테스트와 Mac 네이티브 빌드 워크플로가 포함돼 있습니다. 비공개 배포 서명키, 실제 비밀번호, 개인 설정·영상·진단 자료와 로컬 백업은 제외됩니다. 소스 공개는 새로운 오픈소스 라이선스 허가를 추가하지 않습니다. 포함된 외부 라이브러리의 고지는 설치본에 제공됩니다.

Apple Silicon 빌드는 macOS 15.6 이상에서 Python 3.12와 Homebrew `libusb`를 사용합니다. `requirements-macos.txt`의 바이너리 패키지를 설치하고 `python -m pytest -q`, `python build_macos.py`를 실행합니다. CI는 arm64, Mach-O 최소 운영체제·의존성, ad-hoc 서명, H.264 디코딩·이미지 처리·Cocoa 창·클립보드·고글 미연결 처리를 확인한 후 별도 Mac pkg를 만듭니다. 비공개 배포 서명키는 CI에 전달하지 않습니다.

Windows는 `requirements.txt`, `GogglesHDMI.spec`, Inno Setup과 로컬 `build_release.py`를 사용합니다. 기존 Windows 릴리스 파일은 보관되며 외부 라이브러리의 라이선스 고지는 설치본에 포함됩니다.
