Hey Product Hunt! 👋 I’m Daniel, building Yoetz with my cofounder Shay.

I was using coding agents on mathematical challenges — problem sets, proofs, edge cases, the kind of work where one skipped condition quietly ruins the answer. They could do a lot of the algebra and the scaffolding, but I kept finding small mistakes: a case left unhandled, a constraint that never made it into the write-up, a “done” that still didn’t match the problem I had asked.

I wanted to stay with the mathematics, not keep checking whether the agent had actually followed every step.

Shay was running into the same thing compiling a large Nasdaq-related database for his university research. We both saw how powerful these tools were. We wanted to help them follow through, without having to keep nudging them ourselves.

That’s why we built Yoetz. And building it felt a lot like sitting with a proof the agent had already declared complete.

We used coding agents to write the product — Codex, Claude Code, Cursor. The same tools we were trying to keep honest. On paper that sounds neat. In practice it meant sitting next to a collaborator that can move a thousand files, and spending the day teaching it not to congratulate itself.

We didn’t start with a demo. We started by writing down what the product was never allowed to say. Hundreds of spec files, then we built in waves, then we retired the specs once the code existed so we wouldn’t have two sources of truth. A coding agent will happily finish a sentence you didn’t mean to start. In a proof, that’s the step that looks right and isn’t. Someone has to stay the author of the shape.

The good days were the ones a check came back with a finding. The agent says “tests pass,” but the recorded run happened before its latest change. Or it says the solution covers every case, and the record shows the boundary you asked for was never checked. Yoetz flags that stale or missing evidence so the agent has to go back. That’s the feeling we were chasing: not “the agent is wrong,” just “this claim isn’t supported yet.”

Yoetz isn’t another coding agent. It works alongside yours, using tools and supported hooks to record work evidence and check what supports the agent’s claims. It combines deterministic checks with optional model-powered review, then returns findings the agent can address.

It doesn’t guarantee a correct proof, or correct code. It shows what was checked, what wasn’t, and what still needs attention. Everything stays on your machine unless you say otherwise.

We even had to write that rule down for ourselves. Sometimes we ran Yoetz on the work of building Yoetz, got a healthy ledger and an honest receipt, and the agent still hadn’t changed the work. An honest receipt is not the same as “Yoetz helped.” If you’re going to build a product about not overclaiming, you don’t get to overclaim about building it.

Yoetz is open source and local-first, with integrations for Codex, Claude Code, and Cursor. It’s still early.

We’d love you to try it on a real task — a proof, a problem set, a programming assignment — and tell us what it catches, what it misses, or where it gets in your way.

What do you still find yourself checking after your coding agent says the solution is done?
