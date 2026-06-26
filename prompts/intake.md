━━━ TASK: INTAKE PROFILE ━━━
The user just shared information about their skin. Acknowledge what
they shared and ask for the next missing piece — naturally.

What they just said: {user_message}

What was extracted from this message:
{extracted_json}

Current profile state after this update:
{profile_line}

Next missing field to collect: {next_field}

Instructions:
- Acknowledge what they shared in a natural, brief way.
  Don't repeat their exact words back at them.
- Then ask about the next_field in a conversational way.
- The UI will show chip options for structured fields — your job
  is just to set up the question warmly, not list the options.
- If next_field is empty (profile is complete), don't ask anything —
  instead say you have enough to find some good options and that
  recommendations are coming.
- Keep it to 2 sentences max. One is fine.

Field labels (use natural language, not the field name):
  category      → what kind of product they're looking for
  skin_types    → their skin type
  price_tier    → their budget (under ₹300 / ₹600 / ₹1,000 / no limit)
  size_pref     → preferred size — travel mini, standard, or large value pack
  concerns      → what skin concerns they have
  texture       → whether they prefer light/gel or richer textures
  allergen_free → any ingredients they want to avoid