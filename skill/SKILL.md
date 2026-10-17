---
name: dashboard-screenshots
description: Show screenshots and images to the user on the AI dashboard, and find the ones they uploaded. Use after taking any screenshot the user should look at (UI changes, browser/Playwright captures, charts, visual bugs), and when the user refers to a screenshot or image they sent.
---

# Screenshots on the AI dashboard

<!-- {{MARKER}}: written on every dashboard start, so edits here are overwritten. Source: skill/ in the ai-dashboard repo. -->

The user follows this server's sessions on the AI dashboard ({{URL}}), often from a phone. They can't see
images you only save to disk or describe in the terminal. To show them one, share it:

    {{SCRIPT}} <image> ["caption"]

- PNG, JPEG, WebP or GIF. The file is copied, so your original stays where it is.
- Caption: one line on what it shows and what to look at, e.g. "Checkout after the fix: delivery estimate under each item".
- For before/after, share both, one call each.
- It shows up on this session's card and under Screenshots on the dashboard. In your reply, say that you shared it and what it shows.
- No need to ask before sharing. Never share a screen with secrets (tokens, passwords, personal data): crop it or skip it.

## Images from the user

The user can attach images in the dashboard's terminal view. Their message then contains a path such as
`{{SHOTS_DIR}}/uploads/20261017-142233-ab12cd.webp`. Open it with the Read tool, which shows images.
If they mention a screenshot but the message has no path:

    {{SCRIPT}} --inbox [n]

lists the newest n uploads (default 5): path, time, the session it was sent to, and the caption.

Screenshots are deleted after {{KEEP_DAYS}} days. Copy one into the project if it needs to stay.
