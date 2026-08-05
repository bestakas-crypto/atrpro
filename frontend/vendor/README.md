# frontend/vendor/ -- 서드파티 정적 자산

CDN 미의존(오프라인/네트워크 장애 시에도 차트가 깨지지 않도록) 원칙에 따라
직접 받아서 이 프로젝트 저장소에 커밋해둔 파일들.

## chart.umd.min.js

- **라이브러리**: [Chart.js](https://www.chartjs.org/)
- **버전**: 4.4.4
- **라이선스**: MIT
- **출처**: https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js (다운로드해서 그대로 커밋, 수정 없음)
- **용도**: v1.3 자산추이 차트(`frontend/asset-history.html`)의 총자산/손익/수익률 선형 차트
- **선택 이유**: 이 프로젝트 규모(모바일 개인용 앱)에 Apache ECharts(~350KB+)는
  과함. Chart.js는 UMD 단일 파일(~200KB)로 터치/툴팁/날짜축/반응형을 기본
  지원하고 라이선스가 MIT라 채택함(2026-08-05, v1.3 스펙 10/14절 검토 결과).
