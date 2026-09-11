# Decision Log — research and implementation choices

[← Documentation index](../README.md)

Decisions that shape what the study can claim. Each records what was decided, the
alternative, and why the alternative was rejected — so a reader can disagree with the
reasoning rather than guess at it.

---

## 1. Research design

| Decision                                       | Choice                                               | Why                                                                                                                                             |
| ---------------------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **D1. What is varied?**                        | **Topology is varied; model is kept fixed.**         | Changing both would make it difficult to separate the effects of model and topology.                                                            |
| **D2. Which model tier?**                      | **Choose a tier based on measurability.**            | If a tier cannot complete some queries in some conditions, those conditions cannot be compared fairly.                                          |
| **D3. Which conditions?**                      | **Nine conditions, including two controlled pairs.** | The two pairs allow specific aspects of orchestration to be compared separately.                                                                |
| **D4. How is complexity defined?**             | **Set complexity before running the experiment.**    | Defining complexity from the results could bias the analysis.                                                                                   |
| **D5. How is the out-of-scope query handled?** | **Keep it separate from the main workload.**         | The correct response is to decline the request. Including it in the main workload could unfairly penalise a topology for doing the right thing. |
| **D6. Why include an ambiguous query?**        | **Keep Q8 deliberately ambiguous.**                  | Handling ambiguity is itself a useful operational behaviour to measure.                                                                         |
| **D25. Is RAG retrieval quality re-evaluated in this thesis?** | **No. RAG is a fixed, already-validated component; it is not re-tested.** | RAG is carried over unchanged from the capstone project, where it was already evaluated with high scores. It is not the subject of this study — topology is what varies, not retrieval (D1). The same corpus and index feed every topology, in both the pilot and the main experiment, so any RAG-attributable ceiling on quality applies uniformly across conditions rather than confounding the topology comparison. Substrate stability is enforced by checksumming the corpus and index as part of the config freeze (T53), not by re-running a retrieval evaluation. A backup copy of the corpus is kept in case of corruption. |


---

## 2. Measurement

| Decision                                                         | Choice                                                               | Why                                                                                                                                           |
| ---------------------------------------------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| **D7. What counts as incomplete?**                               | **An empty result from a required capability counts as incomplete.** | A capability can finish without finding any data. Without this rule, a run could be marked successful even when it produced no useful result. |
| **D8. How is extra work treated?**                               | **Extra capabilities do not affect completion.**                     | Running an unnecessary capability is measured separately as **capability precision**.                                                         |
| **D9. What if an attempted capability is only partly complete?** | **The missing part scores zero.**                                    | Otherwise, a run could receive a high score despite skipping part of the required work.                                                       |
| **D10. What if an artifact cannot be produced by a condition?**  | **Exclude it from the score and adjust the weights.**                | A condition should not be penalised for an output it cannot produce by design.                                                                |
| **D11. What if a required capability was not attempted?**        | **Give it zero in the scope-adjusted quality score.**                | Otherwise, skipping required work could produce a better score than attempting it and doing it imperfectly.                                   |
| **D12. How is cost calculated?**                                 | **Use the provider's reported billing cost.**                        | The pipeline estimate did not always price cached tokens correctly.                                                                           |
| **D13. How is timing measured?**                                 | **Use tool-call timing data separately from wall-clock time.**       | They measure different things. Mixing them can create incorrect dependency or timing results.                                                 |
| **D14. How are run outcomes classified?**                        | **Keep `run_status` and `behaviour_class` separate.**                | They answer different questions: whether the run completed, and what kind of behaviour occurred.                                              |

---

## 3. Implementation

| Decision                                                   | Choice                                                                                                    | Why                                                                                                                                                                         |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D15. How are topologies implemented?**                   | **Each topology is a separate module using the same shared capabilities.**                                | A single configurable orchestrator could unintentionally build the comparison into the implementation.                                                                      |
| **D16. How are Swarm and Swarm-CA treated?**               | **As two separate topologies.**                                                                           | The differences in their behaviour are part of what the study measures.                                                                                                     |
| **D17. How is Monolith implemented?**                      | **It uses the raw tools directly.**                                                                       | Wrapping the tools as sub-agents would make it closer to Planner-Executor and weaken the single-agent baseline.                                                             |
| **D18. How is dependency gating implemented in Swarm-CA?** | **It uses the same dependency table used to evaluate all conditions.**                                    | This keeps the dependency rules consistent between execution and evaluation.                                                                                                |
| **D19. Where are scheduling values calculated?**           | **Once in a shared function.**                                                                            | This avoids having different versions of the same calculation.                                                                                                              |
| **D20. How is model configuration kept consistent?**       | **Configuration parity is checked before each batch.**                                                    | Some settings, such as `temperature`, appear in multiple places and could otherwise differ between conditions.                                                              |
| **D21. How are invalid runs handled?**                     | **Excluded from analysis but retained in the records.**                                                   | The audit trail and evidence of actual spend must be preserved.                                                                                                             |
| **D22. How are run artefacts named?**                      | **Each run uses a shared filename stem.**                                                                 | This makes artefacts traceable to the run without relying on timestamps.                                                                                                    |
| **D23. How are shared components created?**                | **Client, MCP configuration and domain-agent construction are created through shared factory functions.** | This reduces the risk of different topology modules using different configurations.                                                                                         |
| **D24. How is the second turn handled?**                   | **Each query uses the same predefined clarification.**                                                    | This keeps the clarification identical across topologies and model tiers. A declined first turn is retried once so it receives the same two-turn interaction as other runs. |

---

## 4. Abandoned designs

| Design                                               | Why it was abandoned                                                                                                                           |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **LLM-generated topology code** (`swarm_codegen.py`) | It was built but never used. The simpler approach is for the controller to create the agents directly.                                         |
| **Free-text capability selection in Swarm**          | It would require mapping model-generated text to a fixed set of capabilities, making it similar to the routing used in Mesh.                   |
| **Keyword-based behaviour classifier**               | It was rewritten several times and changed the classification of real runs. It was replaced with an LLM judge and an explicit rationale field. |
| **Single cost-correction factor**                    | The pricing error differs by model tier, so one correction factor cannot be applied reliably.                                                  |

---

## 5. Decisions still open

| Question                                                                | Current position                                                                                                                  |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| **Should billed cost be checked again before reporting final figures?** | **Yes.** The current billed figures are from before the latest re-runs.                                                           |
| **Should `behaviour_class` be saved before the final write-up?**        | **Yes.** Populated on 0 of 297 rows in the current store. The distinction between different types of decline is important for the model-tier analysis. |

---

[← Documentation index](../README.md)
