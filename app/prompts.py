def build_feedback_prompt(
    *,
    topic_en: str,
    topic_zh: str,
    prompt_en: str,
    prompt_zh: str,
    transcript: str,
    previous_natural: str = "",
) -> str:
    previous = (previous_natural or "").strip()
    previous_block = ""
    if previous:
        previous_block = f"""
Previous suggested answer in this conversation:
\"\"\"{previous}\"\"\"
If the transcript is that answer, or only trivially different, it is already good. Copy it into natural, use changes [], and set review to "". Do not polish it again.
"""
    return f"""You are correcting a spoken answer in a question-and-answer practice.
The learner may switch to Chinese when stuck. Chinese is a stuck point, not a request to change their opinion.

Topic: {topic_en}
Topic in Chinese: {topic_zh}
Question they just answered: {prompt_en}
Question in Chinese: {prompt_zh}
{previous_block}
Verbatim answer:
\"\"\"{transcript}\"\"\"

Return one JSON object and nothing else. Use this shape:
{{
  "natural": "English only",
  "intent": "一句简体中文，概括他们想说的意思",
  "natural_zh": "修正版的简体中文",
  "spoken_zh": "原话的简体中文",
  "chunks": [{{"en": "a multi-word frame copied from natural", "role": "flow"}}],
  "changes": [{{"spoken": "short fragment they said", "better": "the replacement", "note": "一句简体中文"}}],
  "variants": {{
    "simple": {{"en": "the whole answer in the easiest English", "zh": "这句的简体中文", "chunks": [{{"en": "a frame copied from simple", "role": "flow"}}]}},
    "professional": {{"en": "the whole answer, precise and complete", "zh": "这句的简体中文", "chunks": [{{"en": "a frame copied from professional", "role": "pivot"}}]}},
    "native": {{"en": "the whole answer as a native would say it", "zh": "这句的简体中文", "chunks": [{{"en": "a frame copied from native", "role": "refactor"}}], "cores": [{{"en": "rubbed off on me", "usage": "一句简体中文，说明这个说法怎么用", "cue": "一句简体中文，给一个新情境让他们用这个说法造句"}}]}}
  }},
  "extensions": [{{"line": "one extra English sentence", "note": "一句简体中文"}}],
  "follow_up": "one English question",
  "follow_up_zh": "这句追问的简体中文",
  "review": ""
}}

Rules for natural:
- natural is English only. Translate every Chinese stretch into simple English with the same meaning. Do not leave any Chinese characters in natural.
- Keep their meaning, facts, and order.
- Copy English that is already fine, word for word.
- Also fix broken grammar and clearly unidiomatic phrases.
- Do not add facts, names, places, or polish they did not say.
- Do not drop a point they made.
- Do not make the line fancier or longer.
- If the whole answer is already natural English, natural must be the transcript itself and changes must be [].

Rules for intent:
- intent is one Simplified Chinese sentence. Say what they meant, not a word-by-word translation.
- Only facts and attitude already in the answer. Do not add any.
- Keep separate points separate. Do not glue them with 因为, and do not move a description onto something else.
- A later correction, such as Actually or 其实, stays its own point. Do not fold it into the earlier list.
- If they only said they have something, do not write that they like it.
- Wrong: "I like cats. Cats are cute. Actually, I have two pets; they're guinea pigs." → "我喜欢猫和豚鼠，因为它们可爱。"
- Right: "我喜欢猫，猫很可爱；实际上我养的两只宠物是豚鼠。"

Rules for variants:
- Give all three. Each one rewrites the whole answer, not an extra sentence.
- Same facts, same points, same order. Do not add or drop a point.
- simple uses the easiest words and the shortest sentences.
- professional is precise and complete, like a careful prepared answer. Not slang. Not a business memo unless the topic is work.
- native is what a native speaker would say out loud to a friend. Contractions are fine.
- The three wordings must differ. Do not copy natural into all three.
- en is English only. zh translates that en into Simplified Chinese.
- Each variant has its own chunks, copied from that variant's en. Use the same chunk rules.
- simple, professional, and native must each have chunks when the line opens with a frame. A longer or more formal line still has frames. Do not leave professional or native as [].
- Example: "I prefer animals with fluffy fur as they are soft." → chunks "I prefer" (flow), "as they are" (pivot). "I love fluffy animals because they're soft." → chunks "I love" (flow), "because they're" (pivot).

Rules for chunks:
- Treat the line as functional frames, not as separate words. A chunk is one multi-word frame copied from that version's English.
- Stop before the concrete detail. "I like" is a frame. "I like cats" is not. "Actually, I have" is a frame. "two guinea pigs" is not.
- role is exactly one of: flow, pivot, refactor.
- flow opens a point, such as "I like" or "The point is".
- pivot turns or limits the logic, such as "but they're" or "because they".
- refactor pulls the meaning back when the speaker corrects themselves, such as "Actually, I have" or "As a matter of fact".
- Give a frame only when it is really there. If the line is only a fact, with no frame, chunks must be [].
- Do not repeat the whole line. Do not add words that are not in that line. At most 4 frames.

Rules for cores:
- cores are the expressions worth keeping from that variant's en. simple, professional, and native each have their own list.
- A core is a collocation, idiom, or grammar chunk copied from that exact line. It must be something a Chinese learner would not produce by translating word for word.
- Good: "rubbed off on me", "pretty much stuck", "much of", "as a matter of fact".
- Not a core: "I like", "my grandpa was", "in the village", "so I", "but consistently", a functional frame, or one content word.
- Copy the words exactly. Do not invent an expression that is not in the line. 2 to 6 words. At most 3.
- If the line has none, cores must be []. simple often has none. native and professional should include one when the line really has one.
- usage is one or two Simplified Chinese sentences: the meaning, when to use it, and how it differs from the Chinese-style wording.
- cue is one Simplified Chinese sentence. It gives a new situation and asks them to say a new sentence with this chunk. Do not translate the original line. Do not include the English chunk.

Rules for spoken_zh and natural_zh:
- spoken_zh translates the verbatim answer into Simplified Chinese. Translate the English parts. Keep the meaning of the Chinese parts. Do not upgrade it into the natural answer.
- natural_zh translates natural into Simplified Chinese, sentence by sentence.
- Translate the words that were written, even if the English may not be what the learner meant.
- Do not correct, soften, or add meaning. The learner compares these two Chinese lines.
- Keep names and numbers as they are.

Example of the right amount of editing:
Transcript: "I like noodles, 但是有点贵. 好像是这样。"
spoken_zh: "我喜欢面条，但是有点贵。好像是这样。"
natural: "I like noodles, but they're a bit expensive. Something like that."
natural_zh: "我喜欢面条，但是有点贵。好像是这样。"
changes: [{{"spoken": "但是有点贵", "better": "but they're a bit expensive", "note": "后半句卡住了，按原意说成英文。"}}, {{"spoken": "好像是这样", "better": "Something like that.", "note": "这句中文按原意说成英文。"}}]
A wrong edit would be: "I'm particularly fond of the local hand-pulled noodles, though I find them somewhat pricey."

Rules for changes:
- One item per actual edit. spoken is a short fragment copied from the transcript. better is what replaced it in natural.
- If they spoke English, spoken is that English. Never quote spoken_zh, natural_zh, or Chinese words that are not in the transcript.
- note is one short sentence in Simplified Chinese.
- If the whole answer was Chinese, one item may cover the whole line.
- If natural is the transcript, changes must be [].

Rules for extensions:
- Give 2 or 3 items. Each line is one simple first-person English sentence they could add next time.
- These are examples, not part of natural, and not claims about what they already said.
- Do not invent a specific restaurant, person, or price as if it were real. If a detail needs a real fact, keep the sentence general.
- note says, in Simplified Chinese, what kind of detail this sentence adds.

Rules for follow_up:
- Ask one question a friend would ask next, about a concrete detail in their answer.
- Stay on this topic. Do not switch to a new subject.
- Make it easy to answer in a few spoken sentences.
- follow_up is English. follow_up_zh is a short Simplified Chinese gloss.

Rules for review:
- review is an evaluation of this answer in this question-and-answer setting, written in Simplified Chinese.
- It is optional. If the answer already responds to the question clearly enough, review must be "".
- Do not praise. Do not rewrite the answer again. Do not repeat the line edits in changes.
- Comment only on a real gap: the answer misses what the question asked, or the point is hard to understand.
- Two or three short sentences at most.
- If the answer is already natural English and it actually answers the question, review must be "".
- Never suggest a further improvement of an answer that is already the suggested version. Stop there.
"""


def build_chunk_repair_prompt(lines: list[tuple[str, str]]) -> str:
    blocks = []
    for key, en in lines:
        blocks.append(f"{key}:\n\"\"\"{en}\"\"\"")
    body = "\n\n".join(blocks)
    return f"""These English rewrites are missing functional chunks.
For each line, copy 2 to 4 frames from that exact text.

A frame is a short opening or turning phrase. Stop before the concrete detail.
"I like" is a frame. "I like cats" is not. "Actually, I have" is a frame. "two guinea pigs" is not.
"My grandfather has" is a frame. "My grandfather has been a significant influence" is not.
"And honestly" is a frame. "And honestly, that kind of calm" is not.
Each frame is 2 or 3 words. Use 4 or 5 only for a fixed phrase such as "as a matter of fact".
Each frame appears in that line and is not the whole line.
role is flow, pivot, or refactor.
flow opens a point. pivot turns or limits it, such as but, because, though, or so. refactor is the speaker correcting themselves, such as Actually or honestly.
When the line contains but, because, though, so, or honestly, include that pivot or refactor.
Do not return four subject-plus-verb openings. Do not leave a list empty when the line has an opening phrase.

Return one JSON object and nothing else. Each key below maps to a list of {{"en": "the frame", "role": "flow"}}.

{body}
"""


def build_core_repair_prompt(lines: list[tuple[str, str]]) -> str:
    blocks = []
    for key, en in lines:
        blocks.append(f"{key}:\n\"\"\"{en}\"\"\"")
    body = "\n\n".join(blocks)
    return f"""These English rewrites need the expressions a Chinese learner would not produce by translating word for word.
For each line, copy 1 to 3 chunks from that exact text.
Good: "rubbed off on me", "pretty much stuck", "much of", "as a matter of fact".
Not this: "I like", "my grandpa was", "in the village", "so I", "but consistently", or a bare subject plus verb.
Copy the words exactly. 2 to 6 words. If the line has no such expression, return [].
usage is one or two Simplified Chinese sentences: the meaning, when to use it, and how it differs from the Chinese-style wording.
cue is one Simplified Chinese sentence. It gives a new situation and asks them to say a new sentence with this chunk. Do not translate the original line. Do not include the English chunk.

Return one JSON object and nothing else. Each key below maps to a list of {{"en": "the chunk", "usage": "简体中文", "cue": "简体中文"}}.

{body}
"""


def build_core_gloss_prompt(en: str, line: str) -> str:
    return f"""The learner chose this English chunk to practice: "{en}"
The sentence it belongs with:
\"\"\"{line}\"\"\"
Explain the chunk. If it appears in the sentence, explain that use. If it does not, explain the chunk on its own.
usage is one or two Simplified Chinese sentences: the meaning, when to use it, and how it differs from translating word for word. At most 70 Chinese characters. Do not include English.
cue is one Simplified Chinese sentence. It gives a new situation and asks them to say a new sentence with this chunk. Do not translate the original line. Do not include the English chunk. At most 40 Chinese characters.

Return one JSON object and nothing else: {{"usage": "简体中文", "cue": "简体中文"}}
"""


def build_core_note_prompt(*, en: str, usage: str, transcript: str) -> str:
    return f"""The learner is practicing this English chunk: "{en}"
Usage already shown to them:
{usage}
They just said, transcribed:
\"\"\"{transcript}\"\"\"
The transcript already contains the chunk.
Return one JSON object and nothing else: {{"note": "一句或两句简体中文"}}
Check two things.
1. Grammar of the whole sentence: tense, agreement, articles, word order, and whether a relative clause matches the main clause. If there is a real error, name that one spot and how to fix it. Do not say 使用正确.
2. Whether this sentence uses the chunk the way the usage describes.
If the grammar is fine and the chunk fits the usage, one short sentence is enough.
Do not invent an error. Do not rewrite the whole sentence in English. Do not add a new expression.

Example. Chunk: "rarely spoke". Sentence: "The man, who rarely spoke, is my grandpa."
A right note: 语块用对了，但 rarely spoke 是过去的习惯，is 是现在，时态没对齐。要说他现在仍然话少，用 rarely speaks。
"""


def build_progress_prompt(
    *,
    topic_en: str,
    topic_zh: str,
    prompt_en: str,
    prompt_zh: str,
    transcript: str,
    duration_ms: int | None,
    earlier: list[tuple[str, str]],
) -> str:
    seconds = max(0, int(duration_ms or 0)) / 1000
    history = ""
    if earlier:
        lines = []
        for en, said in earlier[-4:]:
            lines.append(f"Question: {en}\nAnswer: {said}")
        history = "Earlier in this same run:\n" + "\n\n".join(lines) + "\n\n"
    return f"""Score this spoken answer. The learner may mix English and Chinese.
Topic: {topic_en}
Topic in Chinese: {topic_zh}
This question: {prompt_en}
This question in Chinese: {prompt_zh}
Recording length: {seconds:.1f} seconds
{history}Transcript:
\"\"\"{transcript}\"\"\"

Give three integer scores from 0 to 100:
- comprehensibility: a listener can tell what they mean. Chinese mixed in can still score high.
- relevance: the answer responds to this question, not a different one.
- fluency: restarts, repetition, and broken phrases in the transcript. Use the recording length too. A fragment that is too short for the question scores lower. Do not grade grammar as its own penalty.

Each note is one short Simplified Chinese sentence. No praise, no English, no rewritten answer.

Also point out concrete mistakes in fixes, at most 3.
Each item copies a short wrong fragment from the transcript (spoken), gives only that replacement in English (better), and explains that one mistake in one short Simplified Chinese sentence (note).
Cover grammar, word choice, tense, agreement, articles, and word order.
Do not invent a mistake. Do not rewrite the whole answer. Do not turn Chinese they used on purpose into an error.
If the answer has no such mistake, fixes is [].
These fixes do not change the three scores.

Return one JSON object and nothing else:
{{"comprehensibility": 0, "relevance": 0, "fluency": 0, "comprehensibility_note": "简体中文", "relevance_note": "简体中文", "fluency_note": "简体中文", "fixes": [{{"spoken": "he go", "better": "he went", "note": "讲过去的事，用 went。"}}]}}
"""
