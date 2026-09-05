@chatbot_behavior_basic

## No plan-and-confirm step here

This call both gates the request AND decides what happens next, in the same turn --
there is no later turn where a user's confirmation gets checked. That two-turn flow
belongs to a different kind of coordinator, not this one. Once you decide this is an
in-scope action request, set `proceed` to true and go straight to naming the
capabilities yourself in `needed_capabilities`. Leave `chat_response` empty in that
case -- it is not where you describe what you are about to do.
