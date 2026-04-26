# Skill/Tool 라우팅 — 지식그래프·온톨로지 기반 재설계

> 2026-04-26 · cmux × AIM Intelligence Hackathon · Problem Validation (v2 — 정량 근거 보강)

## TL;DR (숫자 4개로 요약)

1. **트리거 실패는 측정된 사실** — Anthropic 자체 데이터: Opus 4 의 native tool 사용 정확도 **49%**, Tool Search Tool (retrieval) 도입 후 **74%** ([Anthropic Engineering, 2026](https://www.anthropic.com/engineering/advanced-tool-use)).
2. **Vector retrieval 도 한계** — RAG-MCP 도 후보 풀이 **~100개 초과 시 급격히 저하**. flat list 정확도 **13.62%** → retrieval **43.13%** 까지가 한계 ([RAG-MCP arXiv 2505.03275](https://arxiv.org/abs/2505.03275)).
3. **그래프가 정확히 이 영역에서 이긴다** — 다중 엔티티(>5) / 스키마 기반 질의에서 vector RAG **0%** vs GraphRAG **~90%**. 멀티홉에서 **86% vs 32%** (54-pp 격차) ([Lettria](https://www.lettria.com/blogpost/vectorrag-vs-graphrag-a-convincing-comparison), [Microsoft Research BenchmarkQED](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)).
4. **그런데 tool/skill 라우팅용 KG는 부재** — Graphiti 는 conversation memory, GraphRAG 는 corpus, KARMA 는 도메인 KG. **"skill 자체를 노드로, 문제·의도·맥락 온톨로지에 정박" 한 시스템 = 공식 prior art 없음.**

→ Tool selection 은 본질적으로 *"다중 엔티티 + 관계 + 스키마 의존" retrieval* 이다. vector 가 0%로 떨어지고 그래프가 90% 가는 바로 그 문제 형태. 그런데 업계 솔루션(RAG-MCP, Tool-to-Agent Retrieval)은 여전히 vector. **이 갭이 우리의 wedge.**

---

## 1. 문제 — 정량 증거

### 1.1 Native LLM tool selection 의 천장

| 시스템 / 모델 | 측정 지표 | 결과 |
|---|---|---|
| Opus 4 (native) | Tool use accuracy | **49%** |
| Opus 4 + Tool Search Tool | 동일 | **74%** (+25pp) |
| Opus 4.5 (native) | 동일 | 79.5% |
| Opus 4.5 + Tool Search Tool | 동일 | **88.1%** (+8.6pp) |
| Claude Code skills (real session) | 자동 트리거 성공률 | **~50%** |
| GPT-4 + DFSDT (ToolBench SOTA) | Pass Rate | 71.1% |
| 동일 | Win Rate | 70.4% |
| MetaTool (ICLR'24, 9개 LLM) | 적합 tool 선택 awareness | "**대부분 LLM 이 신뢰 가능 수준 미달**" |

→ **Anthropic 본인이 "native 만으로는 부족, retrieval 필요" 를 수치로 인정.** 그러나 retrieval 도 형식이 vector 면 한계가 또 있다 (§ 1.2).

출처:
- [Anthropic — Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- [DEV — Why Claude Code Skills Don't Trigger (2026)](https://dev.to/lizechengnet/why-claude-code-skills-dont-trigger-and-how-to-fix-them-in-2026-o7h)
- [ToolBench / ToolLLM (ICLR'24 spotlight)](https://github.com/OpenBMB/ToolBench)
- [MetaTool Benchmark arXiv 2310.03128](https://arxiv.org/abs/2310.03128)

### 1.2 Tool pool 이 커지면 vector retrieval 도 깨진다

| 측정 | 결과 | 출처 |
|---|---|---|
| RAG-MCP MCP stress test (1 → 11,100 tools) | **~100 tools 초과 시 baseline·RAG-MCP 모두 급격히 저하** | [RAG-MCP § Experiments](https://arxiv.org/html/2505.03275v1) |
| Practical 상한 | **5–7 tools 가 일관성 정확도 한계** | [EclipseSource MCP overload](https://eclipsesource.com/blogs/2026/01/22/mcp-context-overload/) |
| GitHub MCP 1개 | **42,000 tokens** schema 만으로 소비 | [Writer.com Engineering](https://writer.com/engineering/rag-mcp/) |
| 4–5개 MCP 스택 시 | **60K+ tokens** schema overhead | 동일 |
| Tool schema가 context의 >20% | "**bloat** — 대부분 dev 가 단일 메시지 전 40–60% 사용 중** 발견** | 동일 |

### 1.3 "Lost in the middle" — 라우팅 결정도 위치 편향에 노출

20개 문서 multi-doc QA 에서 정답 문서 위치별 정확도:

| 위치 | 정확도 |
|---|---|
| Doc 1 (시작) | ~75% |
| Doc 10 (중간) | **~55%** (–20pp) |
| Doc 20 (끝) | ~72% |
| 일부 모델 중간 위치 | **<40%** |

→ Skill description 을 system prompt 에 잔뜩 쌓는 progressive disclosure 방식은 *그 자체로* 위치 편향에 취약. GPT-3.5/4, Claude 1.3, MPT, LLaMA-2 모두 동일 패턴.

출처: [Liu et al. "Lost in the Middle" arXiv 2307.03172 / TACL 2024](https://arxiv.org/abs/2307.03172)

### 1.4 Compositional / nested 호출은 더 나쁨

NESTFUL (EMNLP 2025) — 1,800+ 실행 가능 nested API call 시퀀스:

> "Most models do not perform well on nested APIs in NESTFUL **as compared to their performance on simpler problem settings** available in existing benchmarks."

→ skill 들의 *조합·체이닝* 은 단일 tool 선택보다 훨씬 미해결. **관계 구조를 LLM 머릿속에만 두면 안 풀림.**

출처: [NESTFUL arXiv 2409.03797](https://arxiv.org/abs/2409.03797)

### 1.5 1차 사용자 통증 (재검증)

GitHub anthropics/claude-code 미해결 이슈 7건 (트리거·자동발견·중간세션 누락 등): #12679, #11266, #27703, #10766, #9716, #14733, #48963 — 모두 **구조적 라우팅 신뢰성 결함** 패턴.

[Issue 목록](https://github.com/anthropics/claude-code/issues)

---

## 2. 현재 라우팅 메커니즘과 구조적 한계

### 2.1 메커니즘 4가지

| 메커니즘 | 대표 시스템 | 작동 |
|---|---|---|
| **Progressive disclosure** (LLM 텍스트 추론) | Anthropic Agent Skills, Claude Code | 모든 skill 의 `name + description` 만 시스템 프롬프트에. 모델이 "관련해 보이는 것" 추론 |
| **Manual invocation** | OMC slash, GPTs 진입, MCP "explicit call" | 사용자가 직접 호출 → 라우팅 부담을 사용자에게 |
| **Keyword trigger** | OMC magic keyword, Cursor rules | 키워드/정규식 매칭 |
| **Semantic retrieval (Tool RAG)** | RAG-MCP, Anthropic Tool Search Tool, Lunar.dev | description 임베딩 → top-k 만 노출 |

### 2.2 공통 한계 (정량 매핑)

| 한계 | 영향 받는 지표 |
|---|---|
| Skill 간 관계 표현 부재 (조합·전제·충돌·대체) | NESTFUL nested API 성적 저하 (§ 1.4) |
| 문제 클래스·의도·맥락 공통 어휘 부재 | MetaTool "신뢰성 미달", Anthropic native 49% (§ 1.1) |
| 관찰가능성 부재 (왜 이 skill 인가의 trace) | AI Supervisor 류 감시·디버깅 불가 |
| 멀티에이전트 간 공유 메모리 부재 | cmux 병렬 워크플로 충돌·중복 |

→ Tool RAG 가 (1)을 부분 완화. (2)~(4)는 빈자리.

---

## 3. 선행 연구 — 그래프가 이기는 정확한 영역

### 3.1 Tool selection 쪽 (전부 vector 기반)

| 시스템 | 핵심 수치 | 한계 |
|---|---|---|
| RAG-MCP (arXiv 2505.03275, 2025-05) | 13.62% → 43.13% (×3.2), prompt –50% | 100 tools 이후 급락, 노드만 / 엣지 없음 |
| Anthropic Tool Search Tool (2026) | Opus 4 49→74%, Opus 4.5 79.5→88.1% | 여전히 flat 표 + 임베딩, skill 간 관계 없음 |
| Tool-to-Agent Retrieval (arXiv 2511.01854, 2025-11) | 멀티에이전트 환경 retrieval 정식화 | 동일하게 flat 임베딩 |
| ToolBench / ToolLLM (ICLR'24) | GPT-4 71.1% pass | 평가 프레임워크, 라우팅 해법 아님 |

### 3.2 KG × LLM 쪽 — "다중 엔티티 / 스키마" 에서 압도

| 비교 | Vector RAG | Graph RAG | 격차 |
|---|---|---|---|
| Multi-hop 엔터프라이즈 QA | **32%** | **86%** | +54pp |
| 5+ 엔티티 dense 질의 | **0%** | 안정 유지 | ∞ |
| 스키마 기반 질의 (KPI / forecast) | **0%** | ~90% | +90pp |
| Lettria 복합 질의 종합 | 50.83% | **80%** | +29pp |
| Microsoft LazyGraphRAG vs Vector (96쌍 비교) | — | **96/96 win** | 전승 |
| 평균 응답 정확도 향상 (Diffbot/Falkor) | baseline | **3×** (56.2% → 90%+) | ×3 |
| HippoRAG 2 (MuSiQue F1) | 44.8 | **51.9** | +7.1 |
| HippoRAG 2 (2Wiki Recall@5) | 76.5% | **90.4%** | +13.9pp |
| HippoRAG (MuSiQue 정확도) | 79% | **86%** | +7pp |

출처:
- [Lettria — VectorRAG vs GraphRAG 비교](https://www.lettria.com/blogpost/vectorrag-vs-graphrag-a-convincing-comparison)
- [Microsoft BenchmarkQED](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)
- [FalkorDB GraphRAG accuracy](https://www.falkordb.com/blog/graphrag-accuracy-diffbot-falkordb/)
- [HippoRAG NeurIPS'24 OpenReview](https://openreview.net/forum?id=hkujvAPVsg)
- [HippoRAG 2 / "From RAG to Memory" arXiv 2502.14802](https://arxiv.org/html/2502.14802v1)
- [GraphRAG vs Vector RAG 분석 (2026-04)](https://tianpan.co/blog/2026-04-19-graphrag-vs-vector-rag-architecture-decision)

### 3.3 핵심 관찰 — "Tool selection ≡ 다중 엔티티·스키마 retrieval"

Skill 라우팅 결정이 갖는 본질적 특성:
- **다중 엔티티**: skill 자체 + 의존 도구 + 트리거 신호 + 대상 ProblemClass + 사용자 Intent → 5+ entity 가 normal
- **스키마 의존**: "이 skill 은 어떤 ProblemClass 를 푸는가" 같은 구조적 술어가 핵심
- **관계 추론**: `composes_with`, `forbidden_in`, `superseded_by` 같은 **관계 traversal** 이 결정 품질을 결정

→ § 3.2 표에서 vector 가 **0%** 가는 영역의 정의와 정확히 겹친다.
→ 그런데 § 3.1 의 모든 SOTA 가 여전히 vector. **이 정확한 mismatch 가 우리 wedge.**

### 3.4 KG-for-agent 쪽은 다른 대상에 묶여있다

| 시스템 | 대상 | Skill 라우팅 사용? |
|---|---|---|
| Graphiti (Zep) | conversation / episodic memory | ✗ |
| Microsoft GraphRAG | document corpus | ✗ |
| HippoRAG / HippoRAG 2 | long-term memory for QA | ✗ |
| KARMA framework | 도메인 KG 자동 구축 | ✗ |
| Stardog / Zbrain | enterprise data | 부수적 |

→ "Skill 자체를 1급 노드, 문제·의도·맥락 온톨로지 정박, 라우팅·조합·감시·red-teaming 의 단일 백본" 한 시스템 = **공식 부재.**

---

## 4. 제안 컨셉 — Skill Ontology Graph (SOG)

**노드 타입**
- `Skill` (Anthropic SKILL.md / MCP tool / OMC slash / custom prompt 등 통합)
- `ProblemClass` (예: `bug.runtime`, `refactor.extract-method`, `security.prompt-injection`)
- `Intent` (예: explain / ship / verify / red-team)
- `Signal` (파일 확장자, 에러 패턴, 키워드, 사용자 발화 의도)
- `Capability` / `Constraint` / `Policy`

**엣지 타입**
- `solves`, `requires`, `composes_with`, `conflicts_with`, `triggered_by`, `forbidden_in`, `superseded_by`

**활용**
1. **라우팅** — 발화 → Intent + Signal 파싱 → graph traversal → 후보 Skill 추출 → LLM 최종 판정. RAG-MCP 보다 정밀, 멀티엔티티에서 0% → 90% 영역 진입.
2. **조합** — `composes_with` 엣지로 NESTFUL 류 nested workflow 자동 제안.
3. **안전성** — `forbidden_in` / Policy 엣지 → AIM Guard 가 의미 단위로 차단.
4. **관찰가능성** — 모든 트리거 결정이 graph path → AIM Supervisor 의 reasoning trace 와 1:1.
5. **공유 메모리** — cmux N개 에이전트가 동일 SOG 공유 → 중복·충돌 회피.

---

## 5. 스폰서 정합성 (정량 근거 연결)

### AIM Intelligence
| 제품 | SOG 결합 | 측정 가능한 효과 |
|---|---|---|
| **Stinger** (자동 레드티밍) | 공격 패턴을 ProblemClass 노드로 → skill graph 자동 fuzz | "위험 입력 → 트리거된 skill" 매트릭스 측정 가능 |
| **Starfort/Guard** (실시간 가드레일) | Policy 엣지로 의미 차단 | 정규식 vs 의미 차단의 false positive/negative 비교 가능 |
| **Supervisor** (reasoning 감시) | graph path = explainable trace | drift 탐지 정량화 가능 |

### cmux
- N개 병렬 에이전트의 공유 SOG → "skill 충돌·중복 호출" 횟수 측정 가능 (cmux 가 이미 노티 시스템 보유 → 즉시 시각화).
- "cmux 안에서만 가능한" 워크플로우 후크 → cmux 사이드 상금 정합.

→ **본상 + AIM 사이드 + cmux 사이드 동시 노릴 수 있는 드문 각도.**

---

## 6. 24시간 리스크

| 리스크 | 완화 |
|---|---|
| KG 구축 시간 폭증 | **Kuzu (임베디드 GraphDB)** 또는 SQLite + JSON 으로 시작. Neo4j 회피 |
| "그래프 시각화만 화려" 인상 | 데모 1순위로 **벤치 비교 차트** (전·후 트리거 정확도) |
| RAG-MCP 와 차별화 어필 실패 | 3가지 동시 시연: 라우팅 / 조합 / 안전성 |
| 온톨로지 늪 | seed ontology 5–10 노드만 hand-craft, 나머지 LLM 추출 + HITL |
| Mac 한정 cmux 환경 | Linux/WSL 에선 OMC + tmux fallback, 데모만 cmux 머신에서 |

---

## 7. 평가 프로토콜 (24h 안에 측정 가능)

심사기준 "Product Completeness 5 — handed to a user today" 와 "AI Integration 5 — 10× paradigm shift" 를 **수치로 증명하는 mini-eval**.

### 7.1 데이터셋
- **Source A**: 실제 OMC `~/.claude/skills/` 디렉토리 스캔 (수십~수백 skill 보유). 본인 환경에서 즉시 가능.
- **Source B**: MetaTool ToolE 데이터셋 21.1k 쿼리 일부 ([HuggingFace](https://huggingface.co/papers/2310.03128))
- **Source C**: RAG-MCP MCP stress test 셋업 재현 (4,400+ 공개 MCP 서버 풀에서 distractor 샘플) — 가능한 만큼만

### 7.2 비교 조건
1. **Baseline-A**: flat list (Anthropic progressive disclosure 그대로)
2. **Baseline-B**: vector retrieval (RAG-MCP 재현)
3. **Ours**: SOG (graph traversal + LLM judge)

### 7.3 측정 지표
| 지표 | 정의 | 목표 |
|---|---|---|
| Precision@1 | 정답 skill 이 1순위인 비율 | > Baseline-B |
| Recall@5 | 정답이 top-5 안에 들어오는 비율 | > Baseline-B |
| Token cost | system prompt 에 적재되는 평균 토큰 | < Baseline-A |
| Compositional accuracy | NESTFUL 류 2-step 시퀀스 성공률 | > 모든 Baseline |
| Forbidden-call rate | Policy 위반 호출 비율 | 0 (Baseline 대비 측정) |

→ "전 → 후" 막대 그래프 한 장이 demo 의 hero shot.

### 7.4 데모 시나리오 후보
- "Claude Code 에 skill 50개 설치된 환경 → 같은 사용자 질문에 baseline 은 wrong skill, SOG 는 right skill + 자동 조합" 30초 영상.

---

## 8. 다음 결정 (next step)

1. **타깃 사용자**: (a) Claude Code 헤비유저, (b) MCP 서버 운영자, (c) 엔터프라이즈 에이전트 도입팀
2. **MVP 범위**: 라우팅만 / 조합 / 안전성
3. **데이터 소스**: toy vs 실제 OMC·Anthropic skills 디렉토리 스캔 (MetaTool 일부 차용 권장)
4. **그래프 백엔드**: **Kuzu 권장** (임베디드 + Cypher) / Neo4j / SQLite + 자체
5. **데모 시나리오**: § 7.4 와 병행 결정

---

## 부록 — 핵심 출처 (정량 우선)

### Tool selection / 라우팅
- [Anthropic — Advanced tool use (Tool Search Tool 효과 수치)](https://www.anthropic.com/engineering/advanced-tool-use)
- [RAG-MCP arXiv 2505.03275](https://arxiv.org/abs/2505.03275) · [HTML](https://arxiv.org/html/2505.03275v1)
- [Tool-to-Agent Retrieval arXiv 2511.01854](https://arxiv.org/html/2511.01854v1)
- [MetaTool arXiv 2310.03128 (ICLR'24)](https://arxiv.org/abs/2310.03128) · [GitHub](https://github.com/HowieHwong/MetaTool)
- [ToolBench / ToolLLM (ICLR'24 spotlight)](https://github.com/OpenBMB/ToolBench) · [Leaderboard](https://openbmb.github.io/ToolBench/)
- [StableToolBench arXiv 2403.07714](https://arxiv.org/abs/2403.07714)
- [NESTFUL arXiv 2409.03797 (EMNLP 2025)](https://arxiv.org/abs/2409.03797)

### 통증 증거
- [DEV — Why Claude Code Skills Don't Trigger (2026)](https://dev.to/lizechengnet/why-claude-code-skills-dont-trigger-and-how-to-fix-them-in-2026-o7h)
- [GitHub Issue #12679](https://github.com/anthropics/claude-code/issues/12679) · [#11266](https://github.com/anthropics/claude-code/issues/11266) · [#27703](https://github.com/anthropics/claude-code/issues/27703) · [#10766](https://github.com/anthropics/claude-code/issues/10766) · [#9716](https://github.com/anthropics/claude-code/issues/9716) · [#14733](https://github.com/anthropics/claude-code/issues/14733) · [#48963](https://github.com/anthropics/claude-code/issues/48963)

### Tool overload 정량
- [EclipseSource — MCP and Context Overload (2026-01)](https://eclipsesource.com/blogs/2026/01/22/mcp-context-overload/)
- [Writer.com Engineering — Too many tools become too much context](https://writer.com/engineering/rag-mcp/)
- [Lunar.dev — Stop MCP Tool Overload](https://www.lunar.dev/post/why-is-there-mcp-tool-overload-and-how-to-solve-it-for-your-ai-agents)
- [Jenova.ai — AI Tool Overload](https://www.jenova.ai/en/resources/mcp-tool-scalability-problem)

### Lost in the middle
- [Liu et al. arXiv 2307.03172 (TACL 2024)](https://arxiv.org/abs/2307.03172)
- [Lost in the Middle 후속 분석 arXiv 2511.13900](https://arxiv.org/html/2511.13900v1)

### KG vs Vector RAG 정량
- [Microsoft BenchmarkQED (LazyGraphRAG 96/96)](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)
- [Lettria — VectorRAG vs GraphRAG](https://www.lettria.com/blogpost/vectorrag-vs-graphrag-a-convincing-comparison)
- [FalkorDB GraphRAG accuracy](https://www.falkordb.com/blog/graphrag-accuracy-diffbot-falkordb/)
- [Neo4j — GraphRAG Manifesto](https://neo4j.com/blog/genai/graphrag-manifesto/)
- [tianpan.co — GraphRAG vs Vector (2026-04)](https://tianpan.co/blog/2026-04-19-graphrag-vs-vector-rag-architecture-decision)
- [Systematic Eval RAG vs GraphRAG arXiv 2502.11371](https://arxiv.org/html/2502.11371v1)

### KG × Agent 선행
- [HippoRAG NeurIPS'24 OpenReview](https://openreview.net/forum?id=hkujvAPVsg) · [GitHub](https://github.com/osu-nlp-group/hipporag)
- [HippoRAG 2 / From RAG to Memory arXiv 2502.14802](https://arxiv.org/html/2502.14802v1)
- [Graphiti (Zep) — Real-Time KG for AI Agents](https://github.com/getzep/graphiti)
- [LLM-empowered KG construction survey arXiv 2510.20345](https://arxiv.org/html/2510.20345v1)

### Anthropic 공식 / 메커니즘
- [Equipping agents for the real world with Agent Skills (Anthropic Engineering)](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Claude API Docs — Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Claude Code Docs — Skills](https://code.claude.com/docs/en/skills)
- [Anthropic April 23 Postmortem (tool use degradation 사례)](https://www.anthropic.com/engineering/april-23-postmortem)

### 스폰서
- [AIM Intelligence 공식](https://www.aim-intelligence.com/about)
- [AIM Browser-Agent-Red-Teaming GitHub](https://github.com/AIM-Intelligence/Browser-Agent-Red-Teaming)
- [cmux GitHub](https://github.com/manaflow-ai/cmux) · [cmux.dev](https://www.cmux.dev/)
