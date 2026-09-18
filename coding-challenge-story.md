Hey Product Hunt! 👋 I’m Shay, building Yoetz with my cofounder Daniel.

I was using coding agents to compile a large Nasdaq-related database for my university research. They could do a lot of the work, but I kept finding small mistakes and instructions that hadn’t been followed. I wanted to focus on the research, not keep checking whether the agent had done everything I asked.

Daniel was running into similar problems with his studies. We both saw how powerful these tools were. We wanted to help them follow through, without having to keep nudging them ourselves.

That’s why we built Yoetz. And building it felt a lot like the problem it solves.

We used coding agents to write the product — Codex, Claude Code, Cursor. The same tools we were trying to keep honest. On paper that sounds neat. In practice it meant sitting next to a collaborator that can move a thousand files, and spending the day teaching it not to congratulate itself.

We didn’t start with a demo. We started by writing down what the product was never allowed to say. Hundreds of spec files, then we built in waves, then we retired the specs once the code existed so we wouldn’t have two sources of truth. A coding agent will happily finish a sentence you didn’t mean to start. Someone has to stay the author of the shape.

The good days were the ones a check came back with a finding. The agent says “tests pass,” but the recorded run happened before its latest code change. Yoetz flags that stale evidence so the agent can actually re-run the tests. That’s the feeling we were chasing: not “the agent is wrong,” just “this claim isn’t supported yet.”

Yoetz isn’t another coding agent. It works alongside yours, using tools and supported hooks to record work evidence and check what supports the agent’s claims. It combines deterministic checks with optional model-powered review, then returns findings the agent can address.

It doesn’t guarantee correct code. It shows what was checked, what wasn’t, and what still needs attention. Everything stays on your machine unless you say otherwise.

We even had to write that rule down for ourselves. Sometimes we ran Yoetz on the work of building Yoetz, got a healthy ledger and an honest receipt, and the agent still hadn’t changed the work. An honest receipt is not the same as “Yoetz helped.” If you’re going to build a product about not overclaiming, you don’t get to overclaim about building it.

Yoetz is open source and local-first, with integrations for Codex, Claude Code, and Cursor. It’s still early.

We’d love you to try it on a real task and tell us what it catches, what it misses, or where it gets in your way.

What do you still find yourself checking after your coding agent says it’s done?
