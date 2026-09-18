# Receipts, not promises

*How it felt to build Yoetz*

There is a moment, late in a session with a coding agent, when the work is declared finished.
Tests passed. Files moved. The summary is confident, almost kind. And somewhere quieter, a
question remains: did it do the thing you asked, or a nearby thing that learned to sound like
completion?

Yoetz exists because that question would not leave.

In Hebrew, a *yoetz* is a counselor: someone who sits beside you and tells you what is true, not
what is convenient. That is the whole product. Six operations. A ledger that lives on your
machine. A check that will not let the wording outrun the coverage. A receipt that would rather
say *we did not look* than *it is verified*.

The thing Yoetz refuses to do is the point. It will not tell you the work is correct. It will
tell you exactly what was checked, at what coverage, and what is still open. Building that
refusal — and keeping it, session after session, while the tools around it wanted to be helpful —
is what the last months were.

## Drawings before walls

We did not start by shipping a demo. We started by deciding what must never be said.

Before the first durable line of product code, there were architecture decisions, wire schemas,
honesty rules, and a specification tree with an owner for every planned file. Six hundred and
twenty-six of them. Then we built in named waves: protocol, engine, ports, adapters, service,
clients. When the walls were standing, we retired the drawings rather than keep a second copy of
a living system. The archive is still there if you know where to look. The running product does
not pretend the map is the territory.

That sequence sounds austere. It was also the only way to keep a swarm of agents from inventing a
slightly different product every afternoon. A coding agent is tireless and locally brilliant, and
it will happily complete a sentence you did not mean to start. Spec-first was not ceremony. It
was how a human stayed the author of the shape while machines laid the bricks.

## Building a conscience with the things it was meant to check

Yoetz is a ledger for agent-assisted work. It was also built by agent-assisted work. Codex,
Claude, Cursor — the same hosts the product now meets at the door — wrote tests, reducers,
privacy fences, recovery paths, and the words that must never claim too much.

That recursion is the real story of the making. You sit with a collaborator who can move a
thousand files, and you spend the day teaching it not to congratulate itself. You write rules
that CI can lock: coverage-bounded language; no user content in structural tables or logs; every
network channel independently authorized; a timeout is not a failure. You watch the collaborator
reach for “verified,” and you put the word back on the shelf.

It is strange work, and it is intimate. You are not fighting the model. You are building a room
in which it can be honest, because honesty is not its default register — helpfulness is. The
beautiful days were the ones where a check came back with a finding instead of a green light: the
tests were older than the last edit; the obligation was never evidenced; the receipt would have
lied if we had let it. Those were the days the product was itself.

## Privacy as a wall, not a promise

The other refusal is quieter, and it took as much care.

One local service owns the keys, the decrypted state, the writers, and every path off the
machine. The CLI, the MCP bridge, the terminal interface — they ask; they do not hold. Nothing
leaves unless a human has committed an exact policy, and even then the never-send set is
absolute. An agent cannot quietly exfiltrate a repository by being locally helpful. That is not a
promise in a README. It is a topology: clients never open the vault.

Building that wall, then living inside it while agents proposed “just this once” shortcuts, was
the other half of the feeling. Every tempting convenience was a hole. We kept the holes closed.

## Dogfood, and the sentence we would not allow

We ran the product on the work of building the product. Sometimes the ledger was healthy, the
receipt was honest, and the agent had not actually changed the work. There is a runbook now whose
whole purpose is to stop one false sentence: *Yoetz helped.* An honest receipt is not influence.
A registered tool is not activation. A green check at weak coverage is not done.

Writing that runbook felt like the project looking in a mirror and declining to flatter itself.
If you have spent months teaching a system not to overclaim, you do not get to overclaim about
the teaching.

## What it was like

It was like learning a language that cannot say *verified* until it has earned the word, and
discovering you needed that language too.

It was slow in the places that matter — names, frontiers, the exact shape of a finding that must
never disappear when someone answers it — and fast in the places machines are fast: the waves of
implementation, the tests that lock a reducer, the fixtures that remember a failure so it cannot
quietly become a success.

It was collaborative without being confused about authorship. The agents laid astonishing
amounts of brick. A human decided what the building was *for*, and what it was forbidden to
become. When those two roles blurred, the product got a little less true. When they stayed
distinct, it got sharper.

It is still pre-release. The public claims file still marks the release-gated sentences as not
yet evidenced. Two independent threat reviews have not been completed. Codex integration ships
with an empty tested-version set, recorded as untested rather than supported. That list is not a
footnote. It is the same discipline as the rest of the work: do not let the story run ahead of
the record.

If a coding challenge asks what it was like to build this, the honest answer is also the
beautiful one. We spent the time building a small, stubborn counselor that sits between an agent
and the word *done*. We used the agents to build it. We refused, over and over, to let either of
us say more than we had checked.

The receipt is the artifact. The feeling is the same as the receipt: not triumph. Coverage.
Clarity about what remains open. And a quiet, durable pride that we did not trade that clarity
for a prettier sentence.
