"""The fixed texts the benchmark uses: three sentences spoken by the app's own voice to make the
test audio (about 3, 10 and 30 seconds), and five raw dictation transcripts for the cleanup
model, 8, 20, 45, 90 and 180 words long, written the way people talk: fillers, a false start,
a self-correction and a spoken list. No numbers in the spoken clips, so the word error rate
does not depend on how digits are written."""

from __future__ import annotations

CLIPS: tuple[tuple[str, str], ...] = (
    # (name, text spoken by the voice)
    ("3s", "Please send the updated report to the whole team."),
    ("10s", "I listened to the recording again this morning, and the second half sounds much clearer than the first. "
            "Let us keep that version and remove the long pause near the end."),
    ("30s", "Thank you all for joining the call today. Before we begin, I want to give a short update on the project. "
            "The new version of the website went live last week, and so far the feedback from customers has been very "
            "positive. Most people say the pages load faster and the menus are easier to find. There are still a few "
            "small problems on older phones, and the team is working on those right now. We expect to fix them before "
            "the end of the month."),
)

TRANSCRIPTS: tuple[str, ...] = (
    "um so can you send me the file",
    "uh I think we should meet on Tuesday no wait Wednesday because the room is booked all of Tuesday afternoon",
    "okay so for the trip I need to pack a few things um first the passport second the charger for my laptop and "
    "third uh some warm clothes because it is going to be cold up there and I also need the train tickets too",
    "hi Sarah um thanks for sending the draft I read it last night and I think it is mostly ready uh there are just a "
    "few things I would change so first the introduction is a bit long cut it down to two or three sentences second "
    "the chart on page four is missing its labels and third um the summary at the end should say what we want them "
    "to decide I mean what we are asking for not just what we found let me know what you think",
    "okay so this is the plan for next week um on Monday we start with the planning meeting at ten and I want "
    "everyone to bring their list of open tasks uh then on Tuesday the design team will show the new screens and we "
    "need the developers there too because they always have questions about the details on Wednesday we were going "
    "to do the customer calls no actually those moved to Thursday so Wednesday is free for focused work which is "
    "good because the release notes still need writing so for Thursday the customer calls there are three of them "
    "um one at nine one at eleven and one after lunch and I would like two people on each call one to talk and one "
    "to take notes and then Friday is the review so please have your numbers ready by Thursday evening uh the things "
    "I need are the number of new signups the number of support tickets and how long it took us to answer them on "
    "average I think that covers it for now thanks",
)

TRANSCRIPT_WORDS = (8, 20, 45, 90, 180)
