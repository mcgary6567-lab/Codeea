/* =========================================================
   avatars.js — deterministic illustrated profile pictures
   ---------------------------------------------------------
   Builds a portrait as an inline SVG data-URI, seeded by the
   person's name so the same name always gets the same face.
   No network requests, no photo of any real person.

     Avatars.get('Jessica Miller', 'f')  -> "data:image/svg+xml,..."

   To use real member photos instead, drop files in assets/people/
   and give the comment an `img` property in script.js.
   ========================================================= */
window.Avatars = (function () {
  'use strict';

  var SKIN   = ['#f6d9bf', '#efc7a2', '#e0ad81', '#c98d5f', '#a56a3e', '#7d4c2b'];
  var SHADOW = ['#e6c1a3', '#dcae87', '#c9946a', '#b0764a', '#8d5730', '#653c20'];
  var HAIR   = ['#1d1712', '#33241a', '#5a3a21', '#87542a', '#b5854a', '#dcc292', '#8d8d8d', '#e2e0dd'];
  var SHIRT  = ['#2b3a55', '#0a333a', '#44505f', '#6b2f3a', '#3f5a3b', '#4a3a5c', '#2f4f6b', '#585047'];
  var BG     = [
    ['#e6edf5', '#cbd8e8'], ['#e4f0e9', '#c9dfd3'], ['#f4e9e0', '#e2cfbd'],
    ['#e9e5f3', '#d2cbe7'], ['#f2e7e7', '#ddcaca'], ['#e3eff3', '#c7dee6']
  ];

  function hash(str) {
    var h = 2166136261;
    for (var i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    return h;
  }

  /* Small deterministic PRNG so each trait picks independently. */
  function picker(seed) {
    var s = seed;
    return function (arr) {
      s = (s * 1103515245 + 12345) >>> 0;
      return arr[(s >>> 8) % arr.length];
    };
  }

  function chance(seed, salt, pct) {
    return ((hash(seed + salt) >>> 4) % 100) < pct;
  }

  /* ---------------- hair ---------------- */
  function hairBack(style, c) {
    if (style === 'long')  return '<path d="M22 46c0-19 11-28 28-28s28 9 28 28c0 14-2 26-4 34H62c3-9 4-20 3-30-5 5-13 8-22 8s-16-3-20-7c-1 10 0 20 3 29H26c-2-8-4-20-4-34z" fill="' + c + '"/>';
    if (style === 'mid')   return '<path d="M24 46c0-18 10-27 26-27s26 9 26 27c0 10-1 19-3 25H64c2-7 3-15 2-22-5 5-11 7-16 7s-12-2-16-6c-1 7 0 14 2 21H27c-2-6-3-15-3-25z" fill="' + c + '"/>';
    if (style === 'bun')   return '<circle cx="50" cy="17" r="10" fill="' + c + '"/>';
    return '';
  }

  function hairFront(style, c) {
    switch (style) {
      case 'long':
      case 'mid':
        return '<path d="M28 42c1-14 9-22 22-22s21 8 22 22c-3-7-8-11-14-12-4 4-11 7-18 7-5 0-9 2-12 5z" fill="' + c + '"/>';
      case 'bun':
        return '<path d="M29 42c1-13 9-21 21-21s20 8 21 21c-4-8-11-12-21-12s-17 4-21 12z" fill="' + c + '"/>';
      case 'short':
        return '<path d="M29 44c0-15 8-23 21-23s21 8 21 23c-2-9-6-13-11-14-5 4-13 6-21 6-4 0-8 3-10 8z" fill="' + c + '"/>';
      case 'buzz':
        return '<path d="M30 42c0-13 8-21 20-21s20 8 20 21c-4-8-11-12-20-12s-16 4-20 12z" fill="' + c + '" opacity=".92"/>';
      case 'side':
        return '<path d="M29 43c0-14 8-22 21-22s21 8 21 22c-2-8-6-12-11-13-7 5-17 7-25 6-3 0-5 3-6 7z" fill="' + c + '"/>';
      case 'receding':
        return '<path d="M30 40c2-9 9-15 20-15s18 6 20 15c-3-5-8-8-14-8-2 3-6 5-11 5-6 0-11 1-15 3z" fill="' + c + '" opacity=".9"/>';
      default:
        return '';
    }
  }

  function beard(c) {
    return '<path d="M30 46c0 16 9 26 20 26s20-10 20-26c1 8 0 14-1 18-3 9-10 15-19 15s-16-6-19-15c-1-4-2-10-1-18z" fill="' + c + '" opacity=".95"/>' +
           '<path d="M42 60c2 2 4 3 8 3s6-1 8-3c-1 4-4 6-8 6s-7-2-8-6z" fill="#000" opacity=".18"/>';
  }

  function glasses() {
    return '<g fill="none" stroke="#2f3742" stroke-width="1.6" opacity=".85">' +
           '<circle cx="41" cy="47" r="7"/><circle cx="59" cy="47" r="7"/>' +
           '<path d="M48 46h4M34 45l-4 1M66 45l4 1"/></g>';
  }

  function build(name, gender) {
    var seed = hash(name || 'anon');
    var pick = picker(seed);

    var idx    = seed % SKIN.length;
    var skin   = SKIN[idx];
    var shade  = SHADOW[idx];
    var hair   = pick(HAIR);
    var shirt  = pick(SHIRT);
    var bg     = pick(BG);

    var female = gender === 'f';
    var style  = female
      ? ['long', 'mid', 'bun'][hash(name + 'hs') % 3]
      : ['short', 'buzz', 'side', 'receding'][hash(name + 'hs') % 4];

    var hasBeard   = !female && chance(name, 'b', 45);
    var hasGlasses = chance(name, 'g', 22);

    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">' +
      '<defs>' +
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">' +
          '<stop offset="0" stop-color="' + bg[0] + '"/><stop offset="1" stop-color="' + bg[1] + '"/>' +
        '</linearGradient>' +
        '<radialGradient id="vig" cx="50%" cy="42%" r="70%">' +
          '<stop offset="60%" stop-color="#000" stop-opacity="0"/>' +
          '<stop offset="100%" stop-color="#000" stop-opacity=".16"/>' +
        '</radialGradient>' +
        '<clipPath id="clip"><circle cx="50" cy="50" r="50"/></clipPath>' +
      '</defs>' +
      '<g clip-path="url(#clip)">' +
        '<rect width="100" height="100" fill="url(#bg)"/>' +

        /* shoulders + collar */
        '<path d="M50 74c19 0 34 12 36 30H14c2-18 17-30 36-30z" fill="' + shirt + '"/>' +
        '<path d="M43 76l7 9 7-9c-2-1-4-2-7-2s-5 1-7 2z" fill="#fff" opacity=".14"/>' +

        /* neck */
        '<path d="M42 60h16v13c0 3-3 5-8 5s-8-2-8-5V60z" fill="' + shade + '"/>' +
        '<ellipse cx="50" cy="63" rx="8" ry="4" fill="#000" opacity=".12"/>' +

        hairBack(style, hair) +

        /* ears */
        '<ellipse cx="29" cy="49" rx="4.4" ry="5.6" fill="' + skin + '"/>' +
        '<ellipse cx="71" cy="49" rx="4.4" ry="5.6" fill="' + skin + '"/>' +

        /* head */
        '<path d="M50 21c12 0 20 9 20 22 0 15-9 26-20 26s-20-11-20-26c0-13 8-22 20-22z" fill="' + skin + '"/>' +
        '<path d="M50 21c12 0 20 9 20 22 0 4-1 8-2 12 0-13-7-22-18-22s-18 9-18 22c-1-4-2-8-2-12 0-13 8-22 20-22z" fill="#fff" opacity=".07"/>' +

        (hasBeard ? beard(hair) : '') +

        /* brows */
        '<rect x="36" y="39" width="11" height="2.4" rx="1.2" fill="' + hair + '" opacity=".85"/>' +
        '<rect x="53" y="39" width="11" height="2.4" rx="1.2" fill="' + hair + '" opacity=".85"/>' +

        /* eyes */
        '<ellipse cx="41" cy="47" rx="4.4" ry="3.3" fill="#fff"/>' +
        '<ellipse cx="59" cy="47" rx="4.4" ry="3.3" fill="#fff"/>' +
        '<circle cx="41.4" cy="47.2" r="2.5" fill="#4a3b2f"/>' +
        '<circle cx="59.4" cy="47.2" r="2.5" fill="#4a3b2f"/>' +
        '<circle cx="41.4" cy="47.2" r="1.1" fill="#14100c"/>' +
        '<circle cx="59.4" cy="47.2" r="1.1" fill="#14100c"/>' +
        '<circle cx="42.4" cy="46.1" r=".8" fill="#fff"/>' +
        '<circle cx="60.4" cy="46.1" r=".8" fill="#fff"/>' +

        /* nose + mouth */
        '<path d="M50 49c-1 3-2 5-3 6 1 1 2 1.4 3 1.4s2-.4 3-1.4c-1-1-2-3-3-6z" fill="' + shade + '" opacity=".65"/>' +
        '<path d="M44 60c2 2.6 4 3.8 6 3.8s4-1.2 6-3.8c-1.6 4-3.6 6-6 6s-4.4-2-6-6z" fill="#8c4a44"/>' +
        '<ellipse cx="36" cy="53" rx="3.4" ry="2.2" fill="#e07a6a" opacity=".2"/>' +
        '<ellipse cx="64" cy="53" rx="3.4" ry="2.2" fill="#e07a6a" opacity=".2"/>' +

        hairFront(style, hair) +
        (hasGlasses ? glasses() : '') +

        '<rect width="100" height="100" fill="url(#vig)"/>' +
      '</g></svg>';

    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  }

  var cache = {};

  return {
    get: function (name, gender) {
      var key = name + '|' + gender;
      if (!cache[key]) cache[key] = build(name, gender);
      return cache[key];
    }
  };
})();
