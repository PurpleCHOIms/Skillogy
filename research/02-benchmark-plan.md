# Benchmark Plan — Skill Trigger Rate 를 측정하는 법

> **갱신 (Round 11)**: MetaTool 트랙 폐기. MetaTool 은 API/도구 선택 벤치마크이지 Claude Code SKILL 트리거 벤치마크가 아님 — 도메인 mismatch. 평가는 사용자 본인의 로컬 SKILL.md 생태계만 사용.



> 2026-04-26 · cmux × AIM Intelligence Hackathon · 후속 of 01-problem-validation.md

## TL;DR

**가능합니다.** MetaTool 데이터셋(21k 쿼리, MIT 라이선스)을 들고 와서, 우리가 직접 짠 200줄짜리 러너로 3가지 조건(flat / vector / SOG) 을 같은 데이터에 돌려서 **Precision@1, Recall@5, 토큰비용** 막대그래프 한 장으로 demo hero shot 만들 수 있습니다. 추가로 **사용자 본인의 OMC `~/.claude/skills/` 디렉토리에 같은 평가** 를 돌려서 "당신의 환경에서 X% → Y% 올라간다" 의 감정적 한 방까지 가능합니다.

비용 추정: **Haiku 4.5 사용 시 < $5**, 시간 예산: **러너 + 차트 6–8h**. 24h 중 product UI·pitch에 충분한 여유.

---

## 1. 후보 벤치마크 평가 결과

| 벤치 | 라이선스 | 데이터 규모 | 24h 적합도 | 비고 |
|---|---|---|---|---|
| **MetaTool** ([repo](https://github.com/HowieHwong/MetaTool)) | MIT | 21,127 쿼리 (single 20,630 + multi 497) | ✅ **최적** | 데이터(CSV/JSON)는 plug-and-play. 공식 harness 는 Milvus 등 무거움 → 우회. ICLR'24 신뢰성 |
| **ToolBench** ([repo](https://github.com/OpenBMB/ToolBench)) | Apache 2.0 | 16,464 API · 469k calls | ⚠️ 무거움 | RapidAPI 서버 등록 필요 (폼 작성). 24h엔 셋업 부담 |
| **NESTFUL** ([arXiv](https://arxiv.org/abs/2409.03797)) | — | 1,800+ nested seq | 🟡 보조 | composition 평가용 sub-eval 로만 사용 권장 |
| **RAG-MCP impl** ([fintools-ai/rag-mcp](https://github.com/fintools-ai/rag-mcp)) | 미명시 | — | 참조용 | 우리 vector baseline 구현 시 참고 |

**결론**: MetaTool 데이터 + 우리 러너. ToolBench·NESTFUL 은 시간 남으면 추가.

---

## 2. 평가 설계 (갱신)

### 2.1 단일 트랙: Personal Skill Trigger Rate

| 항목 | 값 |
|---|---|
| Source | 본인 로컬 ~/.claude/skills + ~/.claude/plugins 의 SKILL.md (현재 600+) |
| Sampling | stratified, 200–500 skill (다양한 카테고리·플러그인 출처 커버) |
| Query 합성 | skill 마다 LLM(Haiku) 가 "이 skill 이 발동돼야 자연스러운 사용자 발화" 2~3개 생성 |
| Gold 라벨 | 그 skill 의 name (e.g., `typescript-build-error`) |
| 분량 | 400~1500 (id, query, gold_skill_name) |
| 다양성 보장 | gold skill name unique 수 ≥ 100 |
| Reproducibility | random seed + prompt 템플릿 git commit |

**왜 personal-only**: MetaTool 은 RapidAPI catalog 기반 API 선택 벤치라서 Claude Code SKILL 트리거 의미가 없음. 우리 사용자가 매일 겪는 진짜 문제 = "이 발화에 내 skill 이 트리거되나?" 이고 이걸 직접 측정해야 의미 있음.

### 2.2 비교 조건 (3개, 모두 "SKILL 트리거" 의미)

| 조건 | 구현 | 무엇을 의미하나 |
|---|---|---|
| **Baseline-Native** | 600+ skill 의 name+description 전체를 system prompt 에 평면 적재 → Haiku 가 "이 query 에 어떤 skill 발동?" 답함 | **Claude Code 가 native 로 하는 것 그대로** 시뮬 |
| **Baseline-Vector** | description 임베딩 (`text-embedding-3-small` 또는 sentence-transformers `all-MiniLM-L6-v2`) → cosine top-K → Haiku judge | 현재 SOTA retrieval (RAG-MCP 류) |
| **Ours-SOG** | Neo4j Cypher GraphRAG router (`skill_router.core.router.Router.find_skill`) | 우리 차별점 |

### 2.3 측정 지표

| 지표 | 정의 | 역할 |
|---|---|---|
| **Trigger Accuracy** | top-1 picked skill == gold skill 의 비율 | **HERO METRIC** |
| **Recall@5** | gold 가 top-5 안에 들어오는 비율 | 보조 (router 관용성 측정) |
| **Mean input tokens** | 조건당 query 평균 input token 수 | Baseline-Native 가 비싸다는 점을 정량화 |
| **p95 latency** | 조건당 응답 지연 분포 95-pctile (ms) | 운영성 |
| **95% bootstrap CI** | Trigger Accuracy 의 신뢰구간 | 통계적 유의성 |

**Acceptance gate (US-012 에서 검증)**: `Ours-SOG.trigger_accuracy >= Baseline-Native.trigger_accuracy + 0.10` (절대값 10pp 차이).

### 2.4 모델

- **Eval LLM**: Claude **Haiku 4.5** (저비용, 충분한 성능) — primary
- **검증 재실행**: Claude **Sonnet 4.6** 일부 샘플 — robustness 체크
- **Embedding**: OpenAI `text-embedding-3-small` (저비용·고품질) 또는 로컬 `all-MiniLM-L6-v2` (무료, 인터넷 차단 대비)

---

## 3. 비용·시간 예산

### 3.1 API 비용 (보수적 상한)

| 항목 | 계산 | 비용 |
|---|---|---|
| Track A: 500 쿼리 × 3 조건 × ~2k input + 200 output × Haiku 4.5 ($0.80 / $4 per Mtok) | (3M input × $0.8 + 0.3M output × $4) | **~$3.6** |
| Track B: 100 쿼리 × 3 조건 × 동일 | | **~$0.7** |
| Sonnet robustness 재실행 100쌤플 | $3 / $15 per Mtok | **~$1** |
| Embedding (text-embedding-3-small $0.02/Mtok) | 모든 description 임베딩 | **<$0.1** |
| **합계** | | **< $6** |

→ Hackathon 크레딧·개인 키로 충분.

### 3.2 시간 예산

| 단계 | 시간 |
|---|---|
| MetaTool 데이터 다운·정제 | 0.5h |
| 사용자 OMC skills 스캔 + 쿼리 합성 + 라벨링 | 2h |
| Baseline-A (flat) 러너 | 1h |
| Baseline-B (vector) 러너 | 1.5h |
| SOG: 그래프 스키마 + 추출기 + traversal | **4–6h** ← 핵심 리스크 |
| 메트릭 수집 + 차트 (matplotlib bar / line / cost) | 1.5h |
| 데모 UI 위에 결과 통합 | 1h |
| 버퍼 | 2h |
| **소계** | **13–15h** |

→ 24h 중 9–11h 가 product UI / pitch / 휴식에 남음.

---

## 4. 구현 스켈레톤

### 4.1 디렉토리 (해커톤 레포 기준)

```
Hackathon/
├── research/
│   ├── 01-problem-validation.md
│   └── 02-benchmark-plan.md  ← this
├── eval/
│   ├── data/
│   │   ├── metatool_sample.jsonl       # MetaTool 에서 500개 샘플
│   │   └── personal_skills.jsonl        # 본인 OMC skills 기반 합성
│   ├── baselines/
│   │   ├── flat.py                      # Baseline-A
│   │   └── vector.py                    # Baseline-B
│   ├── sog/
│   │   ├── schema.py                    # 노드/엣지 타입
│   │   ├── extractor.py                 # skill metadata → graph
│   │   └── retriever.py                 # graph traversal + LLM judge
│   ├── metrics.py                       # P@1, R@5, token, ...
│   ├── runner.py                        # 모든 조건 한번에 실행
│   └── results/
│       ├── track-a-results.json
│       ├── track-b-results.json
│       └── chart-hero.png
└── product/                             # SaaS UI (Next.js 등)
```

### 4.2 결과 JSON 스키마

```json
{
  "track": "A",
  "model": "claude-haiku-4-5",
  "conditions": {
    "flat":   { "p_at_1": 0.42, "r_at_5": 0.71, "avg_tokens": 18420 },
    "vector": { "p_at_1": 0.58, "r_at_5": 0.83, "avg_tokens":  3120 },
    "sog":    { "p_at_1": 0.79, "r_at_5": 0.94, "avg_tokens":  2840 }
  },
  "n_queries": 500,
  "timestamp": "2026-04-27T03:14:00Z"
}
```

→ chart 코드는 이 JSON 한 파일만 보면 됨 (재실행·갱신 쉬움).

### 4.3 Hero chart 사양

- **Slide 1**: 3-bar chart, x = condition, y = Precision@1 (Track A 본 데이터)
- **Slide 2**: 같은 차트 Track B (사용자 skills) — 라벨 "당신 환경"
- **Slide 3**: 라인 차트, x = tool pool size (10/50/100/500), y = P@1 — Vector 가 100 이후 무너지고 SOG 가 평탄 유지
- **Slide 4**: token cost 비교 (Flat ≫ 나머지)
- **Slide 5**: forbidden-call rate (SOG = 0)

→ 1번이 hero shot. 나머지는 "왜?" 질문에 즉답용.

---

## 5. Fallback Ladder (시간이 부족할 때 자르는 순서)

거꾸로 read — 위에서부터 살리고 아래부터 버린다.

1. **반드시**: Track A Precision@1 + Recall@5 차트 (3 조건)
2. **거의 반드시**: Token cost 차트
3. **권장**: Track B (개인 skills) 동일 차트
4. **권장**: 라인 차트 (pool size 스케일링)
5. **있으면 좋음**: Compositional / NESTFUL sub-eval
6. **있으면 좋음**: Forbidden-call rate (AIM Guard 정합 demo)
7. **시간 남으면**: Sonnet robustness 검증

→ 1+2 만 살아남으면 데모는 성립. 3 까지 살리면 본상 유리. 6 까지 가면 AIM 사이드 상금 직격.

---

## 6. 합법성·재현성 체크리스트

- [ ] MetaTool MIT 라이선스 명시 (요구사항 충족)
- [ ] 데이터 샘플링 방식·시드 기록 (재현성)
- [ ] 프롬프트 템플릿 git 커밋 (3 조건 모두)
- [ ] 모델 버전·날짜·온도 기록 (`claude-haiku-4-5-20251001`, temp=0)
- [ ] 비용 영수증 캡처 (심사위원 신뢰)

---

## 7. 데모 시나리오 (live)

> 발표자: "이 환경에 50개 skill 있고, 사용자가 'TypeScript 빌드 깨졌어' 라 한다."
> 화면 3분할:
> - **왼쪽 (Flat)**: 골랐는데 `format-code` skill 발동 → ❌
> - **가운데 (Vector)**: `debug` skill 발동 → 🟡 비슷하지만 정확하진 않음
> - **오른쪽 (SOG)**: `debug.build-error.typescript` 노드로 traversal → `tsc-error-resolver` skill + `verify-build` skill 자동 조합 제안 → ✅
>
> 그 다음 차트 슬라이드: "500개 쿼리 평균: Flat 42% → Vector 58% → SOG 79%."

→ 30초 영상 + 정량 차트 = "Demo & Presentation 5점" + "AI Integration 5점" 동시 충족.

---

## 8. 결정 필요 (실행 가기 전)

1. **Track B (개인 skills) 라벨링 인력**: 본인 단독 vs LLM-judge + spot check
2. **그래프 백엔드**: Kuzu (Cypher · 임베디드) vs SQLite + 자체 traversal vs Neo4j Aura free
3. **demo UI 스택**: Next.js (App Router) + shadcn 권장, 또는 Streamlit(파이썬으로 빠름)
4. **온톨로지 seed 작성자**: 본인 hand-craft vs LLM 으로 1차 생성 → 검수
5. **NESTFUL 포함 여부**: composition 차트 필요하면 +2h

---

## 출처

- [MetaTool — GitHub HowieHwong/MetaTool](https://github.com/HowieHwong/MetaTool)
- [MetaTool — arXiv 2310.03128 (ICLR'24)](https://arxiv.org/abs/2310.03128)
- [ToolBench — GitHub OpenBMB/ToolBench](https://github.com/OpenBMB/ToolBench)
- [ToolEval Leaderboard](https://openbmb.github.io/ToolBench/)
- [StableToolBench — arXiv 2403.07714](https://arxiv.org/abs/2403.07714)
- [NESTFUL — arXiv 2409.03797](https://arxiv.org/abs/2409.03797)
- [RAG-MCP impl — fintools-ai/rag-mcp](https://github.com/fintools-ai/rag-mcp)
- [Anthropic — Advanced tool use (Tool Search Tool 수치)](https://www.anthropic.com/engineering/advanced-tool-use)
- [Claude API pricing](https://www.anthropic.com/pricing)
