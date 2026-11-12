# Additional-branch UBI 9.8 source inventory

This expands the earlier 3.5/3.6 source evidence to `main`, `rhoai-2.25`, and
`rhoai-3.4`. It does not change Renovate branch scope, establish migration
completion, or prove application/architecture compatibility with UBI 9.8.

## Scope and method

The capture used the 75 unique repositories mapped to default or candidate
derived distributions in `config.yaml` at konflux-central commit `201fffc0`.
Notebooks, Garak, and the custom build preset were excluded as separate policies.
Each available branch was pinned to the revision below, and all tracked paths
matching the default Dockerfile discovery patterns were captured, not just final
stages or Dockerfiles referenced by a PipelineRun. Missing branches and the
repository inaccessible without authentication are listed explicitly.

The source inventory includes all 283 matching files. Native-policy checks used
the 236-file subset mapped to the default, llm-d, model-registry, or
spark-operator distributions, whose local inheritance from the default is
explicit. The remaining 47 files are captured inventory only: the aipcc and ogx
presets reference external presets whose effective inheritance was not resolved.
Do not interpret their capture as validation of the default policy in those repos.

The existing migration renderer generated synthetic 9.8 fixtures with placeholder
digests. The native Dockerfile harness checked complete rendered output using
each fixture's actual captured branch. Files without a matching synthetic
reference were unchanged controls, not successful update cases. Native parsing
also inventoried the unmodified source files. These are offline parser/policy
checks, not a registry lookup or evidence that downstream migration has happened.

## Results

| Branch | Repositories acquired / attempted | Matching files | Files checked by native-policy harness | Files changed by synthetic migration | Synthetic 9.8 dependency entries |
| --- | --- | --- | --- | --- | --- |
| `main` | 74 / 75 | 145 | 121 | 91 | 113 |
| `rhoai-2.25` | 32 / 75 | 51 | 41 | 38 | 45 |
| `rhoai-3.4` | 42 / 75 | 87 | 74 | 57 | 69 |

Validation passed with the existing local Renovate **43.210.0-rpm** image:
configuration validation, complete-output checks for 236 files, four additional
ARG/platform/CRLF cases, and 360 default/derived image-and-branch combinations.
The renderer performed 224 native digest replacements in the captured fixtures,
plus four in the additional cases. The 227 extracted synthetic dependency entries
are not a replacement-operation count: one replacement can update shared input
used by multiple dependency entries. No captured UBI dependency had a native
`skipReason`; this does not prove all variable references were resolved.

There were 74 missing-branch combinations and three unauthenticated-access
failures, all for `models-perf-benchmark-data`. No credentials were accessed.

## Limitations and adoption gate

- No registry/tag/digest or architecture-manifest verification was performed.
- No application builds or compatibility checks were performed.
- External argfiles and PipelineRun build-argument overrides were not captured or
  evaluated. Effective build bases may differ from Dockerfile defaults; OpenVINO
  remains a known example. This is a source inventory, not a final-build-base audit.
- External presets were not resolved; local-derived combination checks do not
  establish production-equivalent effective configuration.
- Other numbered tags, excluded images, and unsupported variable/source forms
  are not silently converted into coverage claims.
- The original 3.5/3.6 snapshots were not refreshed in this additional-branch run.
- Local harnesses and raw source snapshots remain local evidence, not committed
  tests. A production-equivalent dry-run and downstream migration confirmation
  are still required before adopting the post-migration preset.

## Pinned capture manifest

`Files` counts discovery-pattern matches, including unchanged/irrelevant files.
`Missing branch` is a branch-not-found response; `Authentication required` means
this unauthenticated capture could not establish repository/branch contents.
Revisions make the source capture identifiable without embedding Dockerfiles.

Captured at: `2026-11-12T06:22:19.683258+00:00`.

### main

| Repository | Revision / acquisition status | Files |
| --- | --- | --- |
| `MLServer` | `8c98ea267a7d4719d61576b96f4fb61a240578f8` | 2 |
| `NeMo-Guardrails` | `04e2d18ebb076712d21692357f5f7b71ea47e296` | 1 |
| `agents-operator` | `0a829e74363e82e3de3c3433cf59c71d5896df21` | 2 |
| `ai-gateway-controller` | `88b7a9f88ab257e5c786f01c351c65318c648a36` | 1 |
| `ai-gateway-operator` | `199291fb641d8fb0e373e44a6ea72a4ca12450a0` | 1 |
| `ai-gateway-payload-processing` | `ae5211e2c2301453c4c9b602eb34055320f02289` | 2 |
| `argo-workflows` | `61932b99a4c51025ca6f9bc54529bb95eeb23e78` | 2 |
| `batch-gateway` | `ee91661daacac49692f9a29a4055f957ddc7fc3a` | 3 |
| `codeflare-operator` | `f89edd9f015da4b1f466a7fe0b7a164b7da6c7c9` | 1 |
| `data-connect-hub` | `5e1459e20c2493cd1d97916b95c2b3b98b801c95` | 0 |
| `data-science-pipelines` | `2544b041447ffe6b286bf656844ee7d339dc7760` | 5 |
| `data-science-pipelines-operator` | `d619ab2e3f08860141fe51cb7db19f121bd7e90e` | 1 |
| `distributed-workloads` | `72d08ccad1caa60342017b6ed2993de91724e1f1` | 3 |
| `eval-hub` | `a7090da83943df16861094b520e3ed84f09c48ba` | 1 |
| `eval-hub-contrib` | `aaa309773f5a627b9b0304d3338e66e99479f820` | 2 |
| `feast` | `77a023eb08289264454f5da6e387feff4b2df418` | 3 |
| `feast-module-operator` | `fff5fd7263d62a9a7caabc67e496fbcbf64b5252` | 1 |
| `fms-guardrails-orchestrator` | `db3fb6966908f902615bc775e6a7c15849fc51dc` | 1 |
| `guardrails-detectors` | `a9fcf7646b71b0c90d18d62fa71ec3f739633597` | 2 |
| `ilab-on-ocp` | `f4953864a6b7dd880a4eba9b8f616ccd455c3b29` | 1 |
| `kserve` | `11f4320fe0f41f20a66a8da215176737e9ffa9a7` | 9 |
| `kube-auth-proxy` | `e94fa6a58cf0890464c49a7d41d7d34d23023b36` | 1 |
| `kube-rbac-proxy` | `2a3a8dcd1dbe0d64a77df6ebaad2e22ac9bf05e3` | 1 |
| `kubeflow` | `be67e1f74d3b66c25100b715be4ac91a9db6b911` | 2 |
| `kuberay` | `4f3a387f1435ba27b0b14e302685dfa4b5af31aa` | 2 |
| `kueue` | `40509b26226486a12895c48d73fd1a908ee8fd01` | 2 |
| `llm-d-async` | `e0ab47054be74ca0191a386f541837b028666e43` | 1 |
| `llm-d-batch-gateway-operator` | `6c4e652992994e72a656f23289fbdde33c421179` | 1 |
| `llm-d-kv-cache` | `e1a9a798a87f453d2b6de2d6c4d7164f5d4d9883` | 3 |
| `llm-d-latency-predictor` | `11ff18bf57b35966b603396e4076eed9d20c6af3` | 3 |
| `llm-d-router` | `27802fd6cf25ebe2590ef2a1890fe9f999b6b2cc` | 3 |
| `lm-evaluation-harness` | `3504beb834aa24a27012eb66493f606b4fd9e097` | 1 |
| `mcp-lifecycle-module-operator` | `f4eaa620062377733103147880a0179723db43f9` | 1 |
| `mcp-lifecycle-operator` | `86122b78c9582806c667cc036be4942b1950858c` | 1 |
| `ml-metadata` | `101f2eace5dd8a6939d74858c22c946215c7558f` | 1 |
| `mlflow` | `acc60c5f8920733da8426cdc2e08625aa183c015` | 1 |
| `mlflow-operator` | `9a8fdcb9f95ba059a6dffd986fc50753f6b4b821` | 2 |
| `model-metadata-collection` | `a62dea1f15ebf0a7d2915e73de57cc860fe897d0` | 1 |
| `model-registry` | `8a254f6e2d8c5dbfd5240ba0c66bb27dbb5c79c4` | 2 |
| `model-registry-operator` | `b33bea2484bd9619c7cb429496962c404818e4df` | 1 |
| `modelexpress` | `ac7cb8c990097377d860a9441613a1a086c1e62f` | 2 |
| `modelmesh` | `00447486751a0308265998838b3f1ef4c5941879` | 1 |
| `modelmesh-runtime-adapter` | `f4034005f5733affe0c5a237f92214b3e23acac9` | 1 |
| `modelmesh-serving` | `2751b227f532385b73e407e5a6f09ee61920406a` | 1 |
| `models-as-a-service` | `9fa5cbe95a7330555b5b1e7f6b84b30a7692e291` | 3 |
| `models-perf-benchmark-data` | Authentication required | — |
| `odh-cli` | `072ce6b7152992f002e3b05a3ce939140b39ccda` | 1 |
| `odh-dashboard` | `d93999f673a6c2ca78af6a6343a27769327349ee` | 15 |
| `odh-model-controller` | `45137985f14d9e885d7b469e5e75355c750f7853` | 2 |
| `odh-observability` | `ee943d66a41a9c0cb7b3f92b39ecfc46cfaf8ad5` | 1 |
| `ogx-distribution` | `25b6b59108e5781520fa0e0f28ab271516a8de6c` | 2 |
| `ogx-k8s-operator` | `53b292402875acfdc5d7522ebad45056b7fc45a8` | 2 |
| `openshell` | `f027ff7f2efd681fc141d3f2be07f0354b90bca7` | 6 |
| `openshell-dashboard` | `0575142ba423abcd072456940c0ddba38761b7ef` | 1 |
| `openvino_model_server` | `fcf89f9daceb78b66b1b6bae245a354e361a905e` | 1 |
| `pipelines-components` | `96e3ae783f28a00efbb36f649493a54d6b47e8ae` | 4 |
| `praxis-ai-grid-operator` | `4934f8d77b09925fd3d90b4c532714f3d5a629f7` | 3 |
| `praxis-extproc` | `4d5222458ac945ca45227f93f04d8f325f395ede` | 1 |
| `ray-module-operator` | `b214a6894a7884b1a495993ca844c128e83c4b92` | 1 |
| `rest-proxy` | `cdd1685b674af118a505a77b1de7c1a84b64fe95` | 1 |
| `rhai-db-operator` | `a3a353c8914074019cb49cc6c3dd5d2bbaeb3741` | 1 |
| `rhaii-cluster-validation` | `6c32e9383dc331bd2184eb04697b81190a084c1f` | 2 |
| `rhoai-mcp` | `8752a33f6cf6f1ed67db17817e531023ba6edafe` | 1 |
| `rhods-operator` | `19b6b3fc55366a7291ca068b12a907c3e1f93237` | 2 |
| `spark-operator` | `c4cbd5b3d87f7aacc0654222095ca3e0bb96e5bf` | 2 |
| `trainer` | `eb9ae478a5efd192718f658393019317fc662dd4` | 2 |
| `trainer-operator` | `c05b5670a7bbc735b0a393a1f9941c42ea9444df` | 1 |
| `training-operator` | `f0e8cc62a99c8103bbdc74fc82d86ba0eae453ac` | 2 |
| `trustyai-explainability` | `5615afa361bcdae6cfce265e1439f75e6be4ab62` | 1 |
| `trustyai-service-operator` | `3e5655a42d0d9bfe6ae8e75a4239ecd5275f0755` | 3 |
| `vllm-gaudi` | `6c66cbc77235a1fb60f00f6b37fc652cd6f615c5` | 1 |
| `vllm-orchestrator-gateway` | `e2b378fc4b362fbafe6e82b7f7d65a9d61c7c5c0` | 1 |
| `workbenches` | `8d44b4881b42f45580c21b920e4a0260bb6f2b78` | 1 |
| `workbenches-operator` | `02e2525dad20fa8b39821f8743929a3633c34f55` | 1 |
| `workload-variant-autoscaler` | `68bf979de148f8c2f212cfd901b7754470bb839a` | 1 |

### rhoai-2.25

| Repository | Revision / acquisition status | Files |
| --- | --- | --- |
| `MLServer` | Missing branch | — |
| `NeMo-Guardrails` | Missing branch | — |
| `agents-operator` | Missing branch | — |
| `ai-gateway-controller` | Missing branch | — |
| `ai-gateway-operator` | Missing branch | — |
| `ai-gateway-payload-processing` | Missing branch | — |
| `argo-workflows` | `39e7d0235bea528832576947428f76635e8fa780` | 2 |
| `batch-gateway` | Missing branch | — |
| `codeflare-operator` | `21059df706419e737d1701cfe4f326fde96ed8c4` | 1 |
| `data-connect-hub` | Missing branch | — |
| `data-science-pipelines` | `a580389a5c6cc448476196e141b33ed12e892ac5` | 5 |
| `data-science-pipelines-operator` | `21297589fe42da50561d8d7713df7c77951cd1ce` | 1 |
| `distributed-workloads` | `21c76feb6282f8a51a695f4da3291f3d956b4979` | 0 |
| `eval-hub` | Missing branch | — |
| `eval-hub-contrib` | Missing branch | — |
| `feast` | `772a947af3af9d84519396ced7842480834318dd` | 2 |
| `feast-module-operator` | Missing branch | — |
| `fms-guardrails-orchestrator` | `56f23fd860e6b9fb03b0b72bc4e140361af5a4f8` | 1 |
| `guardrails-detectors` | `61a094cedd356b8511a1fd437cbfb1a5cefcbe84` | 2 |
| `ilab-on-ocp` | `28871cf5b97763649c049762a7e16536ed1f8d42` | 1 |
| `kserve` | `b4f983bc683cf7362157be47634912853593d0fe` | 6 |
| `kube-auth-proxy` | Missing branch | — |
| `kube-rbac-proxy` | Missing branch | — |
| `kubeflow` | `f4f04be98bb6ee44996bb3d970b5f39b06fb5014` | 2 |
| `kuberay` | `8f6ab1673de8d0c32b957eda047275023d68d156` | 2 |
| `kueue` | `b52db40a0cc4dfc6e0bee97f86071cf0eab0fa77` | 2 |
| `llm-d-async` | Missing branch | — |
| `llm-d-batch-gateway-operator` | Missing branch | — |
| `llm-d-kv-cache` | Missing branch | — |
| `llm-d-latency-predictor` | Missing branch | — |
| `llm-d-router` | Missing branch | — |
| `lm-evaluation-harness` | `f1cf210644d8534847d6547e0280583228aaca3c` | 1 |
| `mcp-lifecycle-module-operator` | Missing branch | — |
| `mcp-lifecycle-operator` | Missing branch | — |
| `ml-metadata` | `0ff451914491ef6034083bb31f9b7e091c4703f5` | 1 |
| `mlflow` | Missing branch | — |
| `mlflow-operator` | Missing branch | — |
| `model-metadata-collection` | `7be1007e9b1d0ac761807b45be1852aad43cb8ac` | 1 |
| `model-registry` | `00e708cf02a55064d3d8948b27c1109499422fa6` | 2 |
| `model-registry-operator` | `4427a631287012b43221a39defd333dbf923b5da` | 1 |
| `modelexpress` | Missing branch | — |
| `modelmesh` | `64e73ef6e1f5cc9eccbbe5ba37a491b5a8ab0a95` | 1 |
| `modelmesh-runtime-adapter` | `72ae4326ef5d9cda0b75324a50c9a08bf914accd` | 1 |
| `modelmesh-serving` | `6b43ff5014eb13d35c76d3ee32fb01bca4887d00` | 1 |
| `models-as-a-service` | Missing branch | — |
| `models-perf-benchmark-data` | Authentication required | — |
| `odh-cli` | Missing branch | — |
| `odh-dashboard` | `b56fb0fe59e04cf5333f61005508d0c22cecaa51` | 3 |
| `odh-model-controller` | `a51b9e83c9f2e17eb1d089311170d92b605aed7d` | 1 |
| `odh-observability` | Missing branch | — |
| `ogx-distribution` | Missing branch | — |
| `ogx-k8s-operator` | `c99f0f9c0ffa9cb6f361f22111044b59bee81cca` | 1 |
| `openshell` | Missing branch | — |
| `openshell-dashboard` | Missing branch | — |
| `openvino_model_server` | `1a7c42d8a949567f22f10e86b8018c6a3964c724` | 1 |
| `pipelines-components` | Missing branch | — |
| `praxis-ai-grid-operator` | Missing branch | — |
| `praxis-extproc` | Missing branch | — |
| `ray-module-operator` | Missing branch | — |
| `rest-proxy` | `5b584232f71c696c4da6ffd0833d0a75fe1d3d2f` | 1 |
| `rhai-db-operator` | Missing branch | — |
| `rhaii-cluster-validation` | Missing branch | — |
| `rhoai-mcp` | Missing branch | — |
| `rhods-operator` | `a99fe0037c90971be2a6b78365fa3e0ff331f945` | 1 |
| `spark-operator` | Missing branch | — |
| `trainer` | Missing branch | — |
| `trainer-operator` | Missing branch | — |
| `training-operator` | `9c10a8b948e166550a70a22631af3a5d254841bf` | 2 |
| `trustyai-explainability` | `9c543ff56f9b71c928d31704f7263b0fc9e2d780` | 1 |
| `trustyai-service-operator` | `26c4028075f22e4e221aeec07e76d2cd309b4b56` | 2 |
| `vllm-gaudi` | `447cb263b506eb9d9659dbbc11e3d1ea67f41ffd` | 1 |
| `vllm-orchestrator-gateway` | `d9004dacd40d6dc4c7244092d67f03b30cfea52c` | 1 |
| `workbenches` | Missing branch | — |
| `workbenches-operator` | Missing branch | — |
| `workload-variant-autoscaler` | Missing branch | — |

### rhoai-3.4

| Repository | Revision / acquisition status | Files |
| --- | --- | --- |
| `MLServer` | `c1002c37705f3d603a73d16b97b09f49815084c2` | 1 |
| `NeMo-Guardrails` | `150f7e321c0684a855e9b08c8be12e308864dc71` | 1 |
| `agents-operator` | Missing branch | — |
| `ai-gateway-controller` | Missing branch | — |
| `ai-gateway-operator` | Missing branch | — |
| `ai-gateway-payload-processing` | `72b8650ccae58e170cb499eac6f9461fe0747994` | 1 |
| `argo-workflows` | `a582d7ad6fb8b38cb6401d6b358067c28df4cac7` | 2 |
| `batch-gateway` | `81fcc7a3c1d73d7fe0b77dac9fca7beeae16a011` | 3 |
| `codeflare-operator` | Missing branch | — |
| `data-connect-hub` | Missing branch | — |
| `data-science-pipelines` | `94933323bcec444286d8e70a612ec683cc01bb82` | 5 |
| `data-science-pipelines-operator` | `4c0b9a1fa82ce2bc6142859842a583290bb5740f` | 1 |
| `distributed-workloads` | `0e01ce74d1e819e77d1c1212dfe4606c6ec7e6f2` | 11 |
| `eval-hub` | `31270ee50a0382e12e2a8ab6cf53434d2b6118ab` | 1 |
| `eval-hub-contrib` | Missing branch | — |
| `feast` | `d1cda198f0381ef637ebb1f0578c91ff8f9b8e79` | 2 |
| `feast-module-operator` | Missing branch | — |
| `fms-guardrails-orchestrator` | `739482c85606975790edcb296edc0a827bf6dc69` | 1 |
| `guardrails-detectors` | `5d8d2d17680975abe0128b28f5c0be4f8d92cd1b` | 2 |
| `ilab-on-ocp` | Missing branch | — |
| `kserve` | `8452768fa242a2f9ef98949c106ff35d6f90adaa` | 5 |
| `kube-auth-proxy` | `907942fa8492e8d256d800c386ee7ca6873eac41` | 1 |
| `kube-rbac-proxy` | Missing branch | — |
| `kubeflow` | `b8922c60215ee750efee43744cc1a859c0748bfd` | 2 |
| `kuberay` | `b3f4848ca7e00af25df104641eba6583ec25c061` | 2 |
| `kueue` | Missing branch | — |
| `llm-d-async` | Missing branch | — |
| `llm-d-batch-gateway-operator` | Missing branch | — |
| `llm-d-kv-cache` | `c66633b5a596632be6dc564f97d98dd686a4f42c` | 1 |
| `llm-d-latency-predictor` | Missing branch | — |
| `llm-d-router` | Missing branch | — |
| `lm-evaluation-harness` | `3ac85a6e0b1c82e7e64e51c72e618a2562802d52` | 1 |
| `mcp-lifecycle-module-operator` | Missing branch | — |
| `mcp-lifecycle-operator` | Missing branch | — |
| `ml-metadata` | `f0387d2bb4c73e28bcb3ae62b823c635c4d7db78` | 1 |
| `mlflow` | `91b436220176d24f36ee55d5d0a447efc7f6132d` | 1 |
| `mlflow-operator` | `1d3d50df2273f19d00e71e4a248e7030a1a054e8` | 2 |
| `model-metadata-collection` | `3c7f85577088102ada12d61e960d533019c06e53` | 1 |
| `model-registry` | `02eadf9cd26ba377b3f7ea43a424365c385ca5c3` | 2 |
| `model-registry-operator` | `1a168e357437b413e478b4899e6bbe1bd90ddba9` | 1 |
| `modelexpress` | Missing branch | — |
| `modelmesh` | Missing branch | — |
| `modelmesh-runtime-adapter` | Missing branch | — |
| `modelmesh-serving` | Missing branch | — |
| `models-as-a-service` | `4e711d6fceafd40de1bb6b74aa7d9c6103e3780e` | 2 |
| `models-perf-benchmark-data` | Authentication required | — |
| `odh-cli` | `cc7facb31961a8e68475ae1645248aed95974e99` | 1 |
| `odh-dashboard` | `b03a0daf0329f562b50b6b06834fa3e29cfab792` | 9 |
| `odh-model-controller` | `0fa7b8a70258ba2b2fce75e2804661640b08ebda` | 2 |
| `odh-observability` | Missing branch | — |
| `ogx-distribution` | `1ef7faf2827dc8d3527f6c478a0c4ae4844a07bc` | 1 |
| `ogx-k8s-operator` | `b2f4e61d1329b73362bf9ef95589df39763a26b3` | 1 |
| `openshell` | Missing branch | — |
| `openshell-dashboard` | Missing branch | — |
| `openvino_model_server` | `41914a48beff10b9cd6de813a767d47cac82d42d` | 1 |
| `pipelines-components` | `2fdb6df926043aa52d58ec1cda2086e0f6e8e287` | 3 |
| `praxis-ai-grid-operator` | Missing branch | — |
| `praxis-extproc` | Missing branch | — |
| `ray-module-operator` | Missing branch | — |
| `rest-proxy` | Missing branch | — |
| `rhai-db-operator` | Missing branch | — |
| `rhaii-cluster-validation` | `b39a7ec431100178bb240ad47d7503e06c03a144` | 4 |
| `rhoai-mcp` | Missing branch | — |
| `rhods-operator` | `ffeac05f59ea74e14a03f1fe1f81e8153bf6d37b` | 2 |
| `spark-operator` | `bed9121eec45fbfe748d6fac2d2e862dbf11d38d` | 1 |
| `trainer` | `216bdd913addf7e4dcba2a516d143a5eed33c7ac` | 1 |
| `trainer-operator` | Missing branch | — |
| `training-operator` | `02650a8fb631b18e767a2b2ed54afd3481d082e6` | 2 |
| `trustyai-explainability` | `79c70010e6c135d10e2457db733a8fc21ca9d593` | 1 |
| `trustyai-service-operator` | `73bc1da23d4243f1ec4ee9fb790433230e0bf144` | 2 |
| `vllm-gaudi` | `6741f4886c08a9f2b10a90c62f6158ae3c487041` | 1 |
| `vllm-orchestrator-gateway` | `8719552f45a260ed75fff640d6ba4c89d0d9b007` | 1 |
| `workbenches` | Missing branch | — |
| `workbenches-operator` | Missing branch | — |
| `workload-variant-autoscaler` | `746087c4136b807421fd6762ff0d3f3839f9b2e2` | 1 |
