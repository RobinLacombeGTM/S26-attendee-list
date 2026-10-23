# Slush attendee list: a Claude skill

Build your own list of Slush attendees before the event, with each target's company website, company LinkedIn page and personal LinkedIn profile.

You don't need to write code. Claude runs the steps and asks you for two things: your Slush login in your own browser, and who you want to meet.

## What you need

- Claude Code (desktop app, terminal or VS Code extension). https://claude.com/claude-code
- Python 3 on your computer (Claude checks and installs the one missing package).
- Your own Slush ticket and login.
- Chrome, Edge or Brave.

## Install

Pick one of three ways.

**Ask Claude.** Open Claude Code and paste:

> Install this skill with git clone: https://github.com/RobinLacombeGTM/S26-attendee-list into ~/.claude/skills/slush-attendee-list

Claude asks your permission before writing into `~/.claude/`. Approve it.

**One command.** In a terminal:

```
git clone https://github.com/RobinLacombeGTM/S26-attendee-list ~/.claude/skills/slush-attendee-list
```

**Without git.** Download [slush-attendee-list.zip](https://github.com/RobinLacombeGTM/S26-attendee-list/releases/latest/download/slush-attendee-list.zip), unzip it, and move the `slush-attendee-list` folder into `~/.claude/skills/` (on Windows: `%USERPROFILE%\.claude\skills\`). Create the folder if needed.

Then open Claude Code in an empty folder and type: **Build my Slush attendee list**.

Want to check the code first? [AUDIT.md](AUDIT.md) lists every place the code sends data and every safeguard, with line numbers, plus a test suite you can run in one command.

## What happens

1. Claude shows you how to copy your Slush session from the browser (about 1 minute). The login token goes straight into a local file and never into the chat.
2. Claude exports the directory you can already browse in the Slush app: 10,000+ attendees, about 15 to 20 minutes, one request per second. The login token lasts about 15 minutes, so Claude asks you once for a fresh copy and the export resumes where it stopped.
3. You say who you want to meet (roles, countries, company types, exhibitors). Claude narrows the list to 50 to 300 people.
4. Claude finds the missing websites and LinkedIn profiles with web search. A profile counts as found only when the search result shows both the person's name and their company. The rest is marked for a quick manual check.
5. You get a spreadsheet (`enriched.csv`) with a status and the evidence for every link.

Optional: if you have free accounts on Firecrawl, Apify or Hunter.io, Claude can use them for the profiles web search missed. It shows the cost first and waits for your yes.

## Built-in safeguards

- Uses your own ticket only. Claude refuses a colleague's login or a shared account.
- One request per second, a fixed request budget you approve, and a full stop if Slush refuses or asks to slow down. No retries around a block.
- Your token is never printed or sent anywhere except to Slush. Claude deletes the file after the export.
- Nothing is uploaded, and nobody is contacted. The skill builds a list. Reaching out is up to you.

## Responsible use

The list holds personal data from the Slush directory. Use it to prepare your own meetings at Slush. Don't resell or publish it, honor anyone who asks not to be contacted, and delete it after the event. Check the Slush platform terms before you run the export. This skill is not affiliated with or endorsed by Slush. You run it at your own risk.

## Who made this

Robin Lacombe, Profitable Events. I book qualified meetings for B2B tech teams before trade shows. If you'd rather have the list built and the meetings booked for you: https://profitable-events.com
