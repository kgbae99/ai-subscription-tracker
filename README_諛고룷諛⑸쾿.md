# AI 구독료 계산기 - Render 무료 배포 가이드

## 1. 준비물
- GitHub 계정 (없으면 github.com 에서 무료 가입)
- Render 계정 (render.com 에서 GitHub 계정으로 바로 가입 가능)

## 2. GitHub에 코드 올리기
1. github.com 접속 → 로그인 → 우측 상단 "+" → "New repository" 클릭
2. Repository name: `ai-subscription-tracker` (원하는 이름으로 변경 가능)
3. Public으로 설정 (Render 무료 플랜은 Public repo가 편함) → "Create repository"
4. 방금 만든 저장소 페이지에서 "uploading an existing file" 클릭
5. 이 폴더 안의 모든 파일(app.py, templates 폴더, static 폴더, requirements.txt, Procfile, subscriptions.db)을 통째로 드래그해서 업로드
6. "Commit changes" 클릭

## 3. Render에서 배포하기
1. render.com 접속 → GitHub 계정으로 로그인
2. 대시보드에서 "New +" → "Web Service" 클릭
3. 방금 만든 GitHub 저장소(ai-subscription-tracker) 선택 → "Connect"
4. 아래처럼 설정:
   - Name: 원하는 이름 (URL에 포함됨, 예: my-subscription-app)
   - Region: Singapore (한국과 가장 가까움)
   - Branch: main
   - Runtime: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Instance Type: **Free** 선택
5. "Create Web Service" 클릭
6. 3~5분 정도 빌드/배포가 진행되고, 완료되면 상단에 나오는
   `https://my-subscription-app.onrender.com` 같은 주소로 접속 가능

## 4. 주의사항
- 무료 플랜은 15분간 접속이 없으면 잠들고, 다시 접속 시 30~50초 정도 로딩이 걸립니다.
  (포트폴리오 링크를 보여줄 때는 미리 한번 접속해서 깨워두면 좋습니다)
- 이 배포본은 개인정보 없는 가상의 샘플 데이터로 채워져 있습니다.
- PIN 잠금은 데모 편의를 위해 꺼두었습니다(pin_enabled=0). 필요하면 화면의 "보안 설정"에서 다시 켤 수 있습니다.
