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
  "changes": [{{"spoken": "short fragment they said", "better": "the replacement", "note": "一句简体中文"}}],
  "extensions": [{{"line": "one extra English sentence", "note": "一句简体中文"}}],
  "follow_up": "one English question",
  "follow_up_zh": "这句追问的简体中文",
  "review": ""
}}

Rules for natural:
- Keep their meaning, facts, and order.
- Copy English that is already fine, word for word.
- Change only Chinese stretches, broken grammar, and clearly unidiomatic phrases.
- Do not add facts, names, places, or polish they did not say.
- Do not drop a point they made.
- Do not make the line fancier or longer.
- If the whole answer is already natural English, natural must be the transcript itself and changes must be [].

Example of the right amount of editing:
Transcript: "I like noodles, 但是有点贵."
natural: "I like noodles, but they're a bit expensive."
changes: [{{"spoken": "但是有点贵", "better": "but they're a bit expensive", "note": "后半句卡住了，按原意说成英文。"}}]
A wrong edit would be: "I'm particularly fond of the local hand-pulled noodles, though I find them somewhat pricey."

Rules for changes:
- One item per actual edit. spoken is a short fragment from the transcript. better is what replaced it in natural.
- note is one short sentence in Simplified Chinese.
- If the whole answer was Chinese, one item may cover the whole line.

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
