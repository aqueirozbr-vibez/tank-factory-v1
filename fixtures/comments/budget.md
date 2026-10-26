**Budget stop**: this task has spent $5.40 of its $6.00 budget and the next phase may need up to $1.50; the installation has spent $31.20 of its $54.00 daily cap.

The task carries `factory:budget`. A person with write access can raise its budget once, to at most 2x, with a reason (`factory budget --task <task> --raise-to <usd> --reason "..."`, or the console); removing `factory:needs-human` then resumes it from the phase that stopped. Without a raise it stays blocked (PRD Capability 5).
