# Local evaluation results

Elapsed spans process launch through process-group cleanup; output drain/parsing and verifier time are separate.
Passes require normal process/protocol completion, independent verification and unchanged provided tests. All attempts remain in evidence.
These are task/configuration comparisons; Claude also changes the model. Missing metrics are unavailable, not zero.

Token columns are medians of complete reported protocol usage: full input / cached input subset / output. Missing cache details leave full input unavailable. Sampler events provide separate request-level evidence; auxiliary coverage differs.

Text TTFT, first output and rates are medians of per-run request medians. Text TTFT samples count attempts with visible text; text-free attempts stay unavailable. First output includes text, reasoning or tool deltas. Request tok/s divides reported output tokens by the whole attempt duration including initial wait. Generation-window tok/s uses a client-observed window and is absent when reasoning accounting or streamed events are insufficient. Neither rate is server decode speed.

| Task | Profile | Passes / attempts | Median all (range) | Median passed | Text TTFT (n) | First output | Request tok/s (n) | Generation-window tok/s | Native requests / retries | Tokens I / C / O | Usage samples | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bug-fix | forge-coding-v2 | 1 / 1 | 31.967s (31.967s–31.967s) | 31.967s | 1.528s (1) | 2.440s | 33.4 (5) | 64.5 | 5 / 0 | 15757 / 0 / 995 | 1 / 1 | [1](5a655f27-146a-4788-aa3c-2d89ad741574/result.json) |
| bug-fix | forge-optimized | 1 / 1 | 28.216s (28.216s–28.216s) | 28.216s | 2.068s (1) | 2.802s | 27.8 (6) | 89.5 | 6 / 0 | 17708 / 12288 / 752 | 1 / 1 | [1](5f19f328-bfb1-4397-ada6-ef1030294ff7/result.json) |
| exploration-change | forge-coding-v2 | 1 / 1 | 217.347s (217.347s–217.347s) | 217.347s | 1.339s (1) | 3.481s | 36.7 (8) | 58.2 | 8 / 0 | 37386 / 11776 / 3000 | 1 / 1 | [1](21453b27-a73c-4ac4-96c3-067d151938fa/result.json) |
| exploration-change | forge-optimized | 1 / 1 | 80.437s (80.437s–80.437s) | 80.437s | 2.829s (1) | 5.566s | 36.1 (8) | 81.5 | 8 / 0 | 36277 / 19584 / 2973 | 1 / 1 | [1](a4a6d410-8270-49c1-837c-e8110107345b/result.json) |
| multi-file-feature | forge-coding-v2 | 1 / 1 | 39.456s (39.456s–39.456s) | 39.456s | 1.410s (1) | 1.819s | 39.2 (6) | 58.8 | 6 / 0 | 21639 / 4864 / 1489 | 1 / 1 | [1](251ea6f2-ec91-4de4-9c02-e06a0f515fc5/result.json) |
| multi-file-feature | forge-optimized | 1 / 1 | 45.969s (45.969s–45.969s) | 45.969s | 1.417s (1) | 3.496s | 32.6 (7) | 63.4 | 7 / 0 | 24940 / 10240 / 1328 | 1 / 1 | [1](c190c333-b9f3-4221-9e44-8467ebc5c998/result.json) |
| refactor | forge-coding-v2 | 1 / 1 | 47.190s (47.190s–47.190s) | 47.190s | 2.573s (1) | 2.687s | 32.3 (7) | 62.6 | 7 / 0 | 29760 / 8960 / 1556 | 1 / 1 | [1](39947958-83ab-488d-92a9-3aa6d1b943af/result.json) |
| refactor | forge-optimized | 1 / 1 | 36.242s (36.242s–36.242s) | 36.242s | 2.785s (1) | 2.739s | 28.4 (5) | 66.9 | 5 / 0 | 17929 / 7680 / 1275 | 1 / 1 | [1](c2457703-c16a-43ec-a4ce-a0929783ba48/result.json) |
| test-diagnosis | forge-coding-v2 | 1 / 1 | 46.003s (46.003s–46.003s) | 46.003s | 2.027s (1) | 2.714s | 36.0 (6) | 67.0 | 6 / 0 | 19746 / 3584 / 1630 | 1 / 1 | [1](fb4cae39-cf76-45a3-ae60-05a08348d85d/result.json) |
| test-diagnosis | forge-optimized | 1 / 1 | 34.700s (34.700s–34.700s) | 34.700s | 6.447s (1) | 5.422s | 22.2 (5) | 109.6 | 5 / 0 | 14594 / 7680 / 1036 | 1 / 1 | [1](cd0a5cb9-eef6-49d3-8890-7a887640181a/result.json) |
