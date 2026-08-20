/* =========================================================
   Prometheus AI — VSL landing page script
   ---------------------------------------------------------
     1. Countdown timer (7 days, persisted)
     2. YouTube player + playback-gated CTA reveal
     3. Comment feed — sorting, replies, reactions, load-more,
        live timestamps, visitor's own comments
     4. Live/engagement counters + "posted X ago" clock
     5. Like / Comment / Share — all interactive
     6. Exit-intent modal
   Comment content lives in assets/comments.js.
   ========================================================= */
(function () {
  'use strict';

  var CONFIG = {
    /* ---- Countdown -------------------------------------------------
       Rolling deadline: 7 days from the visitor's first visit, stored
       in localStorage. To use one fixed date for everybody instead,
       set fixedDeadline to an ISO string, e.g. '2026-09-01T23:59:00'. */
    countdownDays: 7,
    countdownHours: 0,
    countdownMinutes: 0,
    fixedDeadline: null,

    /* ---- Video ----------------------------------------------------- */
    youtubeId: 'yKunZ0aOnrU',

    /* Reveal the buy button after this many seconds of playback. */
    ctaRevealSeconds: 30,

    /* Hard fallback: reveal the CTA after this long on the page
       regardless of playback. Set to 0 to disable. */
    ctaFallbackSeconds: 900,

    /* ---- Comments -------------------------------------------------- */
    initialVisible: 8,          /* rest sit behind "View more comments" */
    dripDelay: 9,               /* seconds before the first live comment */
    dripGap: [11, 20],          /* seconds between live comments */

    /* ---- Social proof counters (decorative — wire to real data) ---- */
    viewers: 1204,
    likes: 4200,
    comments: 892,
    shares: 1300,

    /* How long ago the post was published, in minutes.
       Renders as m / h / d — 10 * 24 * 60 shows "10d". */
    postedMinutesAgo: 10 * 24 * 60,

    /* ---- Exit modal ------------------------------------------------ */
    exitEnabled: true,
    exitMinSecondsOnPage: 10,
    exitOncePerSession: true
  };

  var BRAND = { name: 'Prometheus AI Crypto Bot', img: 'assets/logo.png' };

  var LS = {
    deadline: 'prom_vsl_deadline',
    liked:    'prom_vsl_liked',
    shared:   'prom_vsl_shared',
    mine:     'prom_vsl_my_comments',
    cLikes:   'prom_vsl_comment_likes'
  };

  var $  = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
  var rand = function (a, b) { return Math.random() * (b - a) + a; };

  function store(key, val) {
    try {
      if (val === undefined) return localStorage.getItem(key);
      localStorage.setItem(key, val);
    } catch (e) { /* private mode */ }
    return val;
  }

  function ago(min) {
    if (min < 1) return 'Just now';
    if (min < 60) return Math.floor(min) + 'm';
    if (min < 1440) return Math.floor(min / 60) + 'h';
    return Math.floor(min / 1440) + 'd';
  }

  function toast(msg) {
    var el = $('#toast');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(function () {
      el.classList.remove('show');
      setTimeout(function () { el.hidden = true; }, 300);
    }, 2200);
  }

  function svg(paths, opts) {
    var o = opts || {};
    return '<svg viewBox="0 0 24 24" fill="' + (o.fill || 'none') + '"' +
           (o.stroke === false ? '' : ' stroke="currentColor" stroke-width="2"') + '>' + paths + '</svg>';
  }

  /* =========================================================
     1. COUNTDOWN — days : hours : minutes : seconds
     ========================================================= */
  function initCountdown() {
    var clocks = $$('[data-clock]');
    if (!clocks.length) return;

    var deadline;

    if (CONFIG.fixedDeadline) {
      deadline = new Date(CONFIG.fixedDeadline).getTime();
    } else {
      deadline = parseInt(store(LS.deadline), 10);
      var span = ((CONFIG.countdownDays * 24 + CONFIG.countdownHours) * 60 + CONFIG.countdownMinutes) * 60000;
      if (!deadline || isNaN(deadline) || deadline - Date.now() > span) {
        deadline = Date.now() + span;
        store(LS.deadline, String(deadline));
      }
    }

    function paint() {
      var left = Math.max(0, deadline - Date.now());
      var total = Math.floor(left / 1000);
      var parts = {
        d: Math.floor(total / 86400),
        h: Math.floor((total % 86400) / 3600),
        m: Math.floor((total % 3600) / 60),
        s: total % 60
      };

      clocks.forEach(function (clock) {
        Object.keys(parts).forEach(function (k) {
          var el = $('[data-unit="' + k + '"]', clock);
          if (el) el.textContent = pad(parts[k]);
        });
      });

      if (left <= 0) {
        var bar = $('#topbar');
        if (bar) {
          bar.classList.add('is-expired');
          var label = $('.topbar-label', bar);
          if (label) label.textContent = 'Offer window closed';
        }
        return;
      }
      setTimeout(paint, 1000);
    }
    paint();
  }

  /* =========================================================
     2. YOUTUBE PLAYER + CTA GATE
     ========================================================= */
  var ctaShown = false;
  var player = null;
  var duration = 0;

  function showCta() {
    if (ctaShown) return;
    ctaShown = true;
    var cta = $('#ctaTop');
    if (cta) cta.classList.add('visible');
  }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = sec % 60;
    return (h ? h + ':' + pad(m) : pad(m)) + ':' + pad(s);
  }

  function initVideo() {
    var overlay = $('#vslPlaceholder');
    var catcher = $('#vslCatch');
    var sound   = $('#vslSound');
    var soundOn = false;
    var bar  = $('#vslBar');
    var time = $('#vslTime');
    var dur  = $('#vslDur');

    if (CONFIG.ctaFallbackSeconds > 0) {
      setTimeout(showCta, CONFIG.ctaFallbackSeconds * 1000);
    }

    if (!window.YT) {
      var tag = document.createElement('script');
      tag.src = 'https://www.youtube.com/iframe_api';
      document.head.appendChild(tag);
    }

    window.onYouTubeIframeAPIReady = function () {
      player = new YT.Player('ytPlayer', {
        videoId: CONFIG.youtubeId,
        host: 'https://www.youtube-nocookie.com',
        playerVars: {
          /* rel:0 restricts the end-screen related videos to this
             video's own channel (Prometheus AI System) — since 2018
             YouTube no longer allows hiding them entirely. */
          rel: 0,
          autoplay: 1,      /* starts on load … */
          mute: 1,          /* … which browsers only allow while muted */
          controls: 0,      /* no scrub bar, no play/pause */
          disablekb: 1,     /* no keyboard seeking */
          fs: 0,            /* no fullscreen button */
          modestbranding: 1,
          playsinline: 1,
          showinfo: 0,
          iv_load_policy: 3
        },
        events: {
          onReady: function () {
            duration = player.getDuration ? player.getDuration() : 0;
            if (dur && duration) dur.textContent = fmt(duration);

            /* belt and braces: some browsers ignore the autoplay
               playerVar but honour an explicit muted play() */
            try { player.mute(); player.playVideo(); } catch (err) { /* noop */ }

            /* Watch for a few seconds. If the browser blocked autoplay
               the player never leaves UNSTARTED/CUED, so fall back to a
               tap-to-play button. Polled rather than a single timeout so
               a slow-loading player is not mistaken for a blocked one. */
            var tries = 0;
            var watch = setInterval(function () {
              tries++;
              var state = player.getPlayerState ? player.getPlayerState() : -1;
              if (state === YT.PlayerState.PLAYING || state === YT.PlayerState.BUFFERING) {
                clearInterval(watch);
                return;
              }
              if (tries >= 12) {              /* ~6s */
                clearInterval(watch);
                if (catcher) catcher.hidden = true;
                if (overlay) overlay.hidden = false;
              }
            }, 500);
          },
          onStateChange: function (e) {
            if (e.data === YT.PlayerState.PLAYING) {
              if (overlay) overlay.hidden = true;
              startTicker();
            }
          }
        }
      });
    };

    var ticking = false;
    function startTicker() {
      if (ticking) return;
      ticking = true;
      setInterval(function () {
        if (!player || !player.getCurrentTime) return;
        var t = player.getCurrentTime() || 0;
        if (!duration && player.getDuration) {
          duration = player.getDuration();
          if (dur && duration) dur.textContent = fmt(duration);
        }
        if (time) time.textContent = fmt(t);
        if (bar && duration) bar.style.width = (t / duration * 100) + '%';
        if (t >= CONFIG.ctaRevealSeconds) showCta();
      }, 500);
    }

    /* Fallback path: browser blocked autoplay, viewer taps to start. */
    function play() {
      if (overlay) overlay.hidden = true;
      if (player && player.playVideo) {
        player.unMute && player.unMute();
        player.playVideo();
        soundOn = true;
        if (sound) sound.hidden = true;
        if (catcher) catcher.hidden = false;
        startTicker();
      }
    }

    if (overlay) {
      overlay.addEventListener('click', play);
      overlay.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); play(); }
      });
    }

    /* Normal path: video is already playing muted — first tap unmutes. */
    function unmute() {
      if (soundOn) return;
      soundOn = true;
      if (player && player.unMute) { player.unMute(); player.setVolume(100); }
      if (sound) sound.hidden = true;
      if (catcher) catcher.classList.add('is-quiet');
    }

    if (catcher) {
      catcher.addEventListener('click', unmute);
      catcher.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); unmute(); }
      });
    }
  }

  /* =========================================================
     3. COMMENT FEED
     ========================================================= */
  var feed, typing, loadMoreBtn, sortMode = 'relevant';
  var pageStart = Date.now();
  var pool = [], mine = [], visible = 0, dripQueue = [];
  var myLikes = {};

  function commentAge(c) {
    return c.ts + (Date.now() - pageStart) / 60000;
  }

  function badgeEl(kind) {
    if (!kind) return null;
    var b = document.createElement('span');
    b.className = 'badge badge-' + kind;
    b.textContent = kind === 'top' ? 'Top contributor' : 'Member';
    return b;
  }

  function buildComment(c, isReply) {
    var wrap = document.createElement('div');
    wrap.className = 'comment' + (isReply ? ' is-reply' : '');
    if (c.brand) wrap.className += ' is-brand';

    /* Real photo if one exists at c.img, otherwise the generated
       avatar. Drop a file into assets/people/ and it appears — no
       code change needed; if the file is missing the onerror
       handler falls back silently. */
    var avatar = document.createElement('img');
    avatar.className = 'comment-avatar';
    avatar.alt = '';
    avatar.loading = 'lazy';
    if (c.brand) {
      avatar.src = c.img;
    } else {
      avatar.src = window.Avatars.get(c.name, c.g);
      if (c.img) {
        var probe = new Image();
        probe.onload = function () { avatar.src = c.img; };
        probe.src = c.img;
      }
    }

    var col = document.createElement('div');
    col.className = 'comment-col';

    var bubble = document.createElement('div');
    bubble.className = 'comment-bubble';

    var head = document.createElement('div');
    head.className = 'comment-head';

    var name = document.createElement('span');
    name.className = 'comment-name';
    name.textContent = c.name;
    head.appendChild(name);

    if (c.brand) {
      var tick = document.createElement('span');
      tick.className = 'verified';
      tick.title = 'Verified page';
      tick.innerHTML = '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 0a8 8 0 100 16A8 8 0 008 0zm3.7 6.7l-4 4a1 1 0 01-1.4 0l-2-2a1 1 0 111.4-1.4L7 8.6l3.3-3.3a1 1 0 111.4 1.4z"/></svg>';
      head.appendChild(tick);

      var author = document.createElement('span');
      author.className = 'badge badge-author';
      author.textContent = 'Author';
      head.appendChild(author);
    }

    var badge = badgeEl(c.badge);
    if (badge) head.appendChild(badge);

    var text = document.createElement('div');
    text.className = 'comment-text';
    if (/^\[.*\]$/.test(c.text)) text.className += ' is-slot';
    text.textContent = c.text;

    bubble.appendChild(head);
    bubble.appendChild(text);

    /* bubble + the reaction chip that hangs off its corner */
    var bubbleWrap = document.createElement('div');
    bubbleWrap.className = 'bubble-wrap';
    bubbleWrap.appendChild(bubble);

    /* --- meta row --- */
    var meta = document.createElement('div');
    meta.className = 'comment-meta';

    var when = document.createElement('span');
    when.className = 'meta-time';
    when.setAttribute('data-ts', c.ts);
    when.textContent = ago(c.ts);

    var key = c.name + '|' + c.ts;
    var likeCount = (c.likes || 0) + (myLikes[key] ? 1 : 0);

    var like = document.createElement('button');
    like.type = 'button';
    like.className = 'meta-btn' + (myLikes[key] ? ' is-active' : '');
    like.textContent = myLikes[key] ? 'Liked' : 'Like';

    var reactions = document.createElement('span');
    reactions.className = 'reactions';
    reactions.hidden = likeCount <= 0;
    reactions.innerHTML = '<i class="rx rx-like">👍</i><i class="rx rx-love">❤️</i><b></b>';
    reactions.querySelector('b').textContent = likeCount.toLocaleString('en-US');
    bubbleWrap.appendChild(reactions);

    like.addEventListener('click', function () {
      var on = !myLikes[key];
      myLikes[key] = on;
      likeCount += on ? 1 : -1;
      like.classList.toggle('is-active', on);
      like.textContent = on ? 'Liked' : 'Like';
      reactions.hidden = likeCount <= 0;
      reactions.querySelector('b').textContent = likeCount.toLocaleString('en-US');
      reactions.classList.remove('pop');
      void reactions.offsetWidth;
      reactions.classList.add('pop');
      store(LS.cLikes, JSON.stringify(myLikes));
    });

    var reply = document.createElement('button');
    reply.type = 'button';
    reply.className = 'meta-btn';
    reply.textContent = 'Reply';
    reply.addEventListener('click', function () {
      var field = $('#ciField');
      if (!field) return;
      field.value = c.name.split(' ')[0] + ' ';
      field.dispatchEvent(new Event('input'));
      field.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(function () { field.focus(); }, 320);
    });

    meta.appendChild(like);
    meta.appendChild(reply);
    meta.appendChild(when);

    col.appendChild(bubbleWrap);
    col.appendChild(meta);

    /* --- brand reply --- */
    if (c.reply) {
      var replies = document.createElement('div');
      replies.className = 'replies';
      replies.appendChild(buildComment({
        name: BRAND.name, img: BRAND.img, brand: true,
        text: c.reply, ts: Math.max(1, Math.round(c.ts * 0.6)), likes: Math.round((c.likes || 0) / 4)
      }, true));
      col.appendChild(replies);
    }

    wrap.appendChild(avatar);
    wrap.appendChild(col);
    return wrap;
  }

  function sortPool(list) {
    var arr = list.slice();
    if (sortMode === 'relevant') {
      arr.sort(function (a, b) { return (b.likes || 0) - (a.likes || 0); });
    } else if (sortMode === 'newest') {
      arr.sort(function (a, b) { return a.ts - b.ts; });
    } else {
      arr.sort(function (a, b) { return b.ts - a.ts; });
    }
    return arr;
  }

  function renderFeed() {
    if (!feed) return;
    feed.textContent = '';

    var sorted = sortPool(pool);
    var shown = Math.min(visible, sorted.length);

    sorted.slice(0, shown).forEach(function (c) { feed.appendChild(buildComment(c)); });
    mine.forEach(function (c) { feed.appendChild(buildComment(c)); });

    var hidden = sorted.length - shown;
    if (loadMoreBtn) {
      loadMoreBtn.hidden = hidden <= 0;
      var label = $('#loadMoreLabel');
      if (label) label.textContent = 'View ' + hidden + ' more comment' + (hidden === 1 ? '' : 's');
    }

    var total = $('#commentsTotal');
    if (total) total.textContent = pool.length + mine.length;
  }

  var commentCount = 0;
  function bumpCommentCount(n) {
    commentCount += n;
    var el = $('#commentCountDisplay');
    if (el) el.textContent = commentCount.toLocaleString('en-US') + ' Comments';
  }

  function initComments() {
    feed = $('#commentsFeed');
    typing = $('#typingIndicator');
    loadMoreBtn = $('#loadMore');
    if (!feed || !window.COMMENT_DATA) return;

    try { myLikes = JSON.parse(store(LS.cLikes) || '{}'); } catch (e) { myLikes = {}; }

    window.COMMENT_DATA.forEach(function (c) {
      (c.drip ? dripQueue : pool).push(c);
    });

    visible = CONFIG.initialVisible;
    renderFeed();

    /* --- live drip --- */
    function drip() {
      if (!dripQueue.length) { if (typing) typing.hidden = true; return; }
      if (typing) typing.hidden = true;

      var c = dripQueue.shift();
      pool.push(c);
      visible += 1;                       /* a new comment is always shown */
      renderFeed();
      bumpCommentCount(1);

      if (dripQueue.length) {
        setTimeout(function () { if (typing) typing.hidden = false; }, 2200);
        setTimeout(drip, rand(CONFIG.dripGap[0], CONFIG.dripGap[1]) * 1000);
      }
    }
    setTimeout(function () { if (typing) typing.hidden = false; }, (CONFIG.dripDelay - 2.5) * 1000);
    setTimeout(drip, CONFIG.dripDelay * 1000);

    /* --- age the timestamps --- */
    setInterval(function () {
      $$('.meta-time').forEach(function (el) {
        var base = parseFloat(el.getAttribute('data-ts'));
        el.textContent = ago(base + (Date.now() - pageStart) / 60000);
      });
    }, 60000);

    /* --- load more --- */
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', function () {
        visible = pool.length;
        renderFeed();
      });
    }

    /* --- sort menu --- */
    var sortBtn = $('#sortBtn'), sortMenu = $('#sortMenu');
    if (sortBtn && sortMenu) {
      sortBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = sortMenu.hidden;
        sortMenu.hidden = !open;
        sortBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      $$('button', sortMenu).forEach(function (opt) {
        opt.addEventListener('click', function () {
          sortMode = opt.getAttribute('data-sort');
          $('#sortLabel').textContent = opt.textContent;
          sortMenu.hidden = true;
          sortBtn.setAttribute('aria-expanded', 'false');
          renderFeed();
        });
      });
      document.addEventListener('click', function () { sortMenu.hidden = true; });
    }

    /* --- the visitor's own comments --- */
    var form  = $('#commentForm');
    var field = $('#ciField');
    var send  = $('#ciSend');
    var count = $('#ciCount');
    var myAvatar = window.Avatars.get('You', 'n');
    var ciAvatar = $('#ciAvatar');
    if (ciAvatar) ciAvatar.src = myAvatar;

    var saved;
    try { saved = JSON.parse(store(LS.mine) || '[]'); } catch (e) { saved = []; }
    saved.forEach(function (c) {
      mine.push({ name: 'You', img: myAvatar, text: c.text, ts: c.ts || 1, likes: 0 });
    });
    renderFeed();
    bumpCommentCount(saved.length);

    if (field && send) {
      field.addEventListener('input', function () {
        var len = field.value.trim().length;
        send.disabled = len === 0;
        if (count) {
          count.hidden = len === 0;
          count.textContent = len + '/280';
        }
      });
    }

    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var text = field.value.trim();
        if (!text) return;

        mine.push({ name: 'You', img: myAvatar, text: text, ts: 0, likes: 0 });
        renderFeed();
        bumpCommentCount(1);

        saved.push({ text: text, ts: 1 });
        store(LS.mine, JSON.stringify(saved.slice(-20)));

        field.value = '';
        send.disabled = true;
        if (count) count.hidden = true;
        toast('Comment posted');
        feed.lastChild.scrollIntoView({ behavior: 'smooth', block: 'center' });
      });
    }
  }

  /* =========================================================
     4. COUNTERS
     ========================================================= */
  var likeBase = 0, likedByMe = false, shareBase = 0;

  /* Exact counts (not "4.2K") so the visitor sees their own
     like / share land on the counter. */
  function renderLikes() {
    var el = $('#likeCount');
    if (!el) return;
    var n = likeBase + (likedByMe ? 1 : 0);
    el.textContent = n.toLocaleString('en-US') + (likedByMe ? ' · You liked this' : '');
  }

  function renderShares() {
    var el = $('#shareCount');
    if (!el) return;
    el.textContent = shareBase.toLocaleString('en-US') + ' Shares';
  }

  function initCounters() {
    var viewerEl = $('#viewerCount');

    likeBase   = CONFIG.likes;
    shareBase  = CONFIG.shares;
    commentCount = CONFIG.comments;
    likedByMe  = store(LS.liked) === '1';

    renderLikes();
    renderShares();
    bumpCommentCount(0);

    var viewers = CONFIG.viewers;
    var floor = Math.round(CONFIG.viewers * 0.9);
    var ceil  = Math.round(CONFIG.viewers * 1.15);

    (function viewerLoop() {
      viewers += Math.floor(rand(-9, 14));
      if (viewers < floor) viewers = floor + Math.floor(rand(0, 40));
      if (viewers > ceil)  viewers = ceil - Math.floor(rand(0, 40));
      if (viewerEl) viewerEl.textContent = viewers.toLocaleString('en-US');
      setTimeout(viewerLoop, rand(3000, 6000));
    })();

    (function socialLoop() {
      likeBase += Math.floor(rand(0, 2));
      renderLikes();
      setTimeout(socialLoop, rand(5000, 10000));
    })();

    var start = Date.now() - CONFIG.postedMinutesAgo * 60000;
    (function agoLoop() {
      var m = (Date.now() - start) / 60000;
      var el = $('#timeAgo');
      if (el) el.textContent = ago(m);
      setTimeout(agoLoop, 60000);
    })();

    var year = $('#year');
    if (year) year.textContent = new Date().getFullYear();
  }

  /* =========================================================
     5. LIKE / COMMENT / SHARE
     ========================================================= */
  function initActions() {
    var likeBtn  = $('[data-action="like"]');
    var shareBtn = $('[data-action="share"]');
    var sheet    = $('#shareSheet');

    if (likeBtn) {
      if (likedByMe) {
        likeBtn.classList.add('is-active');
        likeBtn.setAttribute('aria-pressed', 'true');
        $('.action-label', likeBtn).textContent = 'Liked';
      }
      likeBtn.addEventListener('click', function () {
        likedByMe = !likedByMe;
        likeBtn.classList.toggle('is-active', likedByMe);
        likeBtn.setAttribute('aria-pressed', likedByMe ? 'true' : 'false');
        $('.action-label', likeBtn).textContent = likedByMe ? 'Liked' : 'Like';
        store(LS.liked, likedByMe ? '1' : '0');
        renderLikes();
        if (likedByMe) {
          likeBtn.classList.remove('pop');
          void likeBtn.offsetWidth;
          likeBtn.classList.add('pop');
        }
      });
    }

    var commentBtn = $('[data-action="comment"]');
    if (commentBtn) {
      commentBtn.addEventListener('click', function () {
        var field = $('#ciField');
        if (!field) return;
        field.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(function () { field.focus(); }, 350);
      });
    }

    function countShare() {
      shareBase += 1;
      renderShares();
      store(LS.shared, '1');
    }

    function closeSheet() {
      if (!sheet) return;
      sheet.hidden = true;
      if (shareBtn) shareBtn.setAttribute('aria-expanded', 'false');
    }

    if (shareBtn && sheet) {
      shareBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        if (navigator.share) {
          navigator.share({ title: document.title, url: location.href })
            .then(countShare)
            .catch(function () { /* dismissed */ });
          return;
        }
        var open = sheet.hidden;
        sheet.hidden = !open;
        shareBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });

      $$('.share-opt', sheet).forEach(function (opt) {
        opt.addEventListener('click', function () {
          var url = encodeURIComponent(location.href);
          var text = encodeURIComponent(document.title);
          var target = {
            facebook: 'https://www.facebook.com/sharer/sharer.php?u=' + url,
            x:        'https://twitter.com/intent/tweet?url=' + url + '&text=' + text,
            whatsapp: 'https://api.whatsapp.com/send?text=' + text + '%20' + url,
            telegram: 'https://t.me/share/url?url=' + url + '&text=' + text
          }[opt.getAttribute('data-share')];

          if (target) {
            window.open(target, '_blank', 'noopener,noreferrer,width=640,height=580');
            countShare();
          } else {
            var done = function () { countShare(); toast('Link copied'); };
            if (navigator.clipboard) {
              navigator.clipboard.writeText(location.href).then(done, done);
            } else {
              var tmp = document.createElement('input');
              tmp.value = location.href;
              document.body.appendChild(tmp);
              tmp.select();
              try { document.execCommand('copy'); } catch (err) { /* noop */ }
              tmp.remove();
              done();
            }
          }
          closeSheet();
        });
      });

      document.addEventListener('click', function (e) {
        if (!sheet.hidden && !sheet.contains(e.target)) closeSheet();
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') closeSheet();
      });
    }
  }

  /* =========================================================
     6. EXIT-INTENT MODAL
     ========================================================= */
  function initExitModal() {
    if (!CONFIG.exitEnabled) return;

    var modal = $('#exitModal');
    var back  = $('#modalBackdrop');
    if (!modal || !back) return;

    var KEY = 'prom_vsl_exit_shown';
    var armedAt = Date.now();
    var shown = false;
    try { shown = CONFIG.exitOncePerSession && sessionStorage.getItem(KEY) === '1'; } catch (e) { /* noop */ }

    function open() {
      if (shown) return;
      if (Date.now() - armedAt < CONFIG.exitMinSecondsOnPage * 1000) return;
      shown = true;
      try { sessionStorage.setItem(KEY, '1'); } catch (e) { /* private mode */ }
      modal.hidden = false;
      back.hidden = false;
      document.body.classList.add('modal-open');
    }

    function close() {
      modal.hidden = true;
      back.hidden = true;
      document.body.classList.remove('modal-open');
    }

    document.addEventListener('mouseout', function (e) {
      if (!e.relatedTarget && e.clientY <= 0) open();
    });

    if (window.history && history.pushState) {
      history.pushState(null, '', location.href);
      window.addEventListener('popstate', function () {
        if (!shown) { history.pushState(null, '', location.href); open(); }
      });
    }

    var lastY = window.pageYOffset;
    window.addEventListener('scroll', function () {
      var y = window.pageYOffset;
      if (lastY - y > 90 && y < 220) open();
      lastY = y;
    }, { passive: true });

    $('#modalClose').addEventListener('click', close);
    $('#modalDecline').addEventListener('click', close);
    back.addEventListener('click', close);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !modal.hidden) close();
    });
  }

  /* =========================================================
     BOOT
     ========================================================= */
  function boot() {
    initCountdown();
    initVideo();
    initCounters();
    initComments();
    initActions();
    initExitModal();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
