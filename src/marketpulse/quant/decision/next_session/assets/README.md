Official DeepSeek V4 tokenizer files, downloaded 2026-10-19 from
https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip
as linked by https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/ .

Only the JSON vocabulary and chat template are used. No downloaded Python or remote
code is executed. SHA256 values are pinned in input_reservation.py. The dependency
is tokenizers 0.22.2. Offline counts are estimates, with a fixed 1,024 token framing
margin; accounting always uses provider usage, and unknown usage retains the reserve.

Calibration on 608 distinct real DeepSeek Flash requests returned differences of
24–50 input tokens between the local chat template and provider usage. This does not
prove future tokenizer identity: a measured input above the reserved input stops
the task, retains actual usage, and requires investigation before further calls.
