/* =========================================================
   comments.js — the comment feed's content
   ---------------------------------------------------------
   17 comments, supplied by the page owner.

   NAMES + PHOTOS ARE PLACEHOLDERS. Each entry should map to
   the real member who actually said it, published with their
   permission. Swap `name`/`g` for the real reviewer, or add
   `img: 'assets/people/whoever.jpg'` to use their photo.
   Keep proof of any result claim on file, and leave the
   "results are not typical" line in the footer.

   Fields:
     name    display name
     g       'm' | 'f'  (seeds the generated profile picture)
     img     optional path to a real photo, overrides the avatar
     badge   'top' | 'member' | null
     ts      minutes ago
     likes   reaction count
     text    the comment ("\n" starts a new line)
     reply   optional reply from the page (verified "Author")
     drip    true = appears live a few seconds after load
   ========================================================= */
window.COMMENT_DATA = [
  {
    name: 'Marcus Bell', g: 'm', img: 'assets/people/01.jpg', badge: 'top', ts: 214, likes: 148,
    text: '⚡ Setup was seamless — I was live in under 5 minutes.\n🤖 Love that it runs my whole strategy for me, hands-free.',
    reply: 'Thanks Marcus! For anyone just starting: the done-for-you setup call is booked from the link in your welcome email — one of our experts sets your bot up with you, live on the call.'
  },
  {
    name: 'Rachel Sanders', g: 'f', img: 'assets/people/02.jpg', badge: 'member', ts: 201, likes: 96,
    text: '📈 Market up or down, this bot adapts instantly.\n🤖 Watching it execute with precision is impressive.'
  },
  {
    name: 'Devin Parker', g: 'm', img: 'assets/people/03.jpg', badge: null, ts: 188, likes: 71,
    text: '🚀 Finally, a crypto bot that feels professional.\n🛡️ Risk controls and a stop on every trade keep my account protected.',
    reply: 'Your funds never leave your own exchange account — the bots connect by API and cannot withdraw. You stay in control of every deposit and withdrawal.'
  },
  {
    name: 'Natalie Brooks', g: 'f', img: 'assets/people/04.jpg', badge: 'top', ts: 176, likes: 112,
    text: '💡 The automation is hands-off — zero manual effort.\n📲 I just check in from my phone now and then.'
  },
  {
    name: 'Andre Whitman', g: 'm', img: 'assets/people/05.jpg', badge: null, ts: 164, likes: 54,
    text: '🔥 Prometheus AI trades smarter than anything I’ve tried.\n⚙️ Clean interface, clear logic, runs around the clock.'
  },
  {
    name: 'Courtney Hale', g: 'f', img: 'assets/people/06.jpg', badge: 'member', ts: 151, likes: 63,
    text: '💻 Connected my account and it was trading right away.\n🌐 The dashboard is sleek and beginner-friendly.',
    reply: 'Glad it was quick! We still recommend starting in demo mode so you can watch the bots run with nothing at risk — switch live only when you’re ready.'
  },
  {
    name: 'Jared Mullins', g: 'm', img: 'assets/people/07.jpg', badge: null, ts: 139, likes: 38,
    text: '🔄 Even during volatility, the bot adjusts beautifully to the swings.\n🛡️ It sticks to the plan when I would have panicked.'
  },
  {
    name: 'Vanessa Cole', g: 'f', img: 'assets/people/08.jpg', badge: 'member', ts: 127, likes: 87,
    text: '🛡️ Risk management is tight — no reckless moves.\n😌 No more emotional trading or staring at charts all day.'
  },
  {
    name: 'Trevor Lindquist', g: 'm', img: 'assets/people/09.jpg', badge: null, ts: 114, likes: 42,
    text: '💎 Transparency matters — every trade is visible.\n📲 I love seeing exactly what it does in real time.'
  },
  {
    name: 'Alicia Moreno', g: 'f', img: 'assets/people/10.jpg', badge: null, ts: 102, likes: 35,
    text: '⚙️ Setup was painless and it just runs.\n👍 Prometheus AI is worth every penny for the time it saves.'
  },
  {
    name: 'Scott Ferraro', g: 'm', img: 'assets/people/11.jpg', badge: 'member', ts: 91, likes: 58,
    text: '🤖 Smooth automation, no constant tweaking required.\n😌 Set it up once and it runs itself in the background.'
  },
  {
    name: 'Bianca Chen', g: 'f', img: 'assets/people/12.jpg', badge: null, ts: 78, likes: 29,
    text: '📊 The trade logic is crystal clear.\n🧠 Smart algorithms working nonstop in the background.'
  },
  {
    name: 'Nolan Pierce', g: 'm', img: 'assets/people/13.jpg', badge: 'top', ts: 64, likes: 104,
    text: '💻 This bot learns and adapts daily.\n🚀 It just keeps getting sharper with time.'
  },
  {
    name: 'Kayla Dominguez', g: 'f', img: 'assets/people/14.jpg', badge: null, ts: 52, likes: 46,
    text: '🌐 Interface is futuristic yet simple to use.\n💡 Perfect balance for beginners and pros alike.'
  },
  {
    name: 'Grant Sullivan', g: 'm', img: 'assets/people/15.jpg', badge: 'member', ts: 38, likes: 51,
    text: '🔄 It trades both bullish and bearish markets.\n💡 That’s the versatility I was looking for.',
    reply: 'That’s the point of running the bots through a full cycle — they trade both directions. All trading carries risk, so only fund what you’re comfortable losing.'
  },
  {
    name: 'Erica Vaughn', g: 'f', img: 'assets/people/16.jpg', badge: null, ts: 21, likes: 33, drip: true,
    text: '⚙️ Prometheus AI is redefining automated trading.\n🚀 Smart, reliable, and built for serious traders.'
  },
  {
    name: 'Damian Foster', g: 'm', img: 'assets/people/17.jpg', badge: 'member', ts: 9, likes: 21, drip: true,
    text: '⚙️ Crypto futures feel effortless with this bot.\n🤖 It just follows its rules without the stress.'
  }
];
