# Human mode — why she stopped sounding like a vending machine

The complaint was exact: *it answers, but it doesn't talk.* That was not the
model's fault. Iris had four mouths and only one of them was ever allowed to
sound like a person.

| What spoke | What it said | Now |
|---|---|---|
| Canned small talk | `"I'm doing great, thanks!"` — and the model never saw the turn, so the thread died on the spot | Off when a model is available; real conversation reaches the model. Still there with zero keys, where instant beats nothing |
| Tool confirmations | `"Done."` Every time. Forever. This is the most frequent path in the whole system | Her own voice: varied, situational, never the same line twice running |
| The model | A four-line prompt asking for "warm, sharp, concise" | A character brief — who she is, how she talks, how she decides |
| Voice output | Cut at 500 characters, mid-thought | `SPEECH_MAX_CHARS`, default 1400 |

Plus the thing that mattered most and was nobody's mouth: you could not
**reply** to her. Wake-word mode threw away anything not prefixed with "Iris",
so every sentence meant summoning her again. That is not a conversation, that
is a queue.

## Set it up

Everything is already on by default. The only line worth adding to `.env`:

```ini
USER_NAME=Prakash
```

So she can use your name the way people do — now and then, for warmth, not in
every sentence.

### Through Gemini (what you are running)

```ini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-flash-latest
VISION_MODEL=gemini-flash-latest
USER_NAME=Prakash
```

Nothing else. `SMALLTALK_ENABLED` defaults to false, so "how are you" reaches
Gemini and comes back as an answer that knows what you were doing five minutes
ago.

### On a local model later

```ini
OPENAI_COMPAT_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_COMPAT_API_KEY=ollama
OPENAI_COMPAT_MODEL=qwen3:8b
LLM_PROVIDER_ORDER=openai_compat,gemini
PROMPT_SUFFIX=/no_think
```

`PROMPT_SUFFIX` is appended verbatim to every system prompt. Qwen3 reasons for
ten seconds before saying "morning" unless you tell it not to; that is what
`/no_think` is for. Remove it for genuinely hard tasks.

The character brief is about 1,500 tokens. With tool schemas and a dozen turns
of history that does not fit Ollama's default 4k window, so start it wider:

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

## What actually changed

**She holds a thread.** The last twelve exchanges ride along on every model
call — enough to remember, few enough that the free tier does not bite.
Everything older is folded into one rolling line of notes in the background, so
she remembers an hour ago and not just the last four things said. Small talk
and memory commands are written to history too; a turn the model never learns
about is a turn it will contradict later.

**She knows the shape of the conversation.** Not from intelligence — from a
clock. The first thing said after an hour away gets greeted. The same command
twice gets noticed. Four commands in twenty seconds gets terser answers,
because that is what a person does when you are clearly in a hurry.

```
you : what time is it
iris: It's 9:07 PM on Thursday, September 17.
you : what time is it
iris: Same again — it's 9:07 PM on Thursday, September 17.
    … an hour passes …
iris: There you are. It's 9:07 PM on Thursday, September 17.
```

**Her confirmations never repeat.** `"Done."` has ten forms and never lands
twice running. The rule underneath is *garnish, never rewrite*: a tool that
reported something real — a temperature, a distance, a name — keeps its own
words exactly, because a paraphrased sensor reading is an invented sensor
reading. Only the packaging moves. The action stays as deterministic as it ever
was, which is the thing the judge was shown.

**You can reply to her.** For nine seconds after she finishes speaking,
anything you say is the next turn — no wake word. Each exchange extends it. Go
quiet and she drops back to wake-word only, so the room's conversation stays
out of her input. Start talking over her and she stops, like a person. Her own
voice coming back through the microphone is never taken as a command.

## Knobs

| Setting | Default | What it does |
|---|---|---|
| `USER_NAME` | — | Your name, used sparingly |
| `SMALLTALK_ENABLED` | `false` | Canned instant pleasantries. Auto-on when no model is configured |
| `PERSONA_ACKS` | `true` | Her voice on tool confirmations. `false` restores flat acks |
| `HISTORY_MAX_TURNS` | `12` | Exchanges carried on each model call |
| `ROLLING_SUMMARY_ENABLED` | `true` | Fold older turns into notes she still knows |
| `RAPPORT_RETURN_GAP_MIN` | `25` | Silence longer than this and she greets you |
| `SPEECH_MAX_CHARS` | `1400` | Longest spoken reply before a clean cut |
| `PROMPT_SUFFIX` | — | Appended to every system prompt, for local model control tokens |

Want the old behaviour back for a demo? `PERSONA_ACKS=false` and
`SMALLTALK_ENABLED=true`.

## Where it lives

`iris/app/agent/persona.py` is the character brief and the voice.
`iris/app/agent/rapport.py` is the clock and the long memory. Neither is
load-bearing: if either breaks, she still answers, she just sounds flatter.
That is deliberate and tested.

## Still on the list

- **Streaming to the voice.** The kernel waits for the whole reply before
  speaking. `cloud.py` already has `stream()`; wiring it sentence by sentence
  into `voice.speak` is the next real latency win.
- **Barge-in for server-side TTS.** Browser speech can be cut off mid-sentence
  today; edge and piper need a `/voice/stop` that kills the playback process.
- **Speaking first.** The camera already greets people it recognises. The same
  hook could mention a reminder that is about to fire, or that the machine has
  been hot for an hour.
