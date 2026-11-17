/*
 * Naadi's YouTube player page. Plain script, no build step (it is uploaded as is to a static Space).
 *
 * Protocol (mirrors apps/web/src/video/bridge.ts; apps/web/src/video/playerPage.test.ts runs this file):
 *   page -> Naadi: hello, ready{rates,rate}, state{state}, time{t,state}, error{code}, api_failed
 *   Naadi -> page: init{videoId,start,parentOrigin}, cmd{play|pause|seek|rate}
 * Every incoming message is checked by source window, EXACT origin and shape before anything is done with it.
 */
(function () {
  "use strict";

  var NS = "naadi:yt";
  var API = "https://www.youtube.com/iframe_api";
  var HOST = "https://www.youtube-nocookie.com";
  var TICK_MS = 500;
  var API_TIMEOUT_MS = 15000;
  var VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
  var MAX_SECONDS = 86400 * 2;

  // The Naadi origins that may embed this page. Loopback hosts (any port) are allowed so local and CI runs work: a
  // page on someone's own machine can embed a player that holds nothing.
  var ALLOWED_PARENTS = ["https://arrowassassin-naadi.hf.space"];
  var LOOPBACK = ["localhost", "127.0.0.1", "[::1]"];

  function isObj(v) {
    return typeof v === "object" && v !== null && !Array.isArray(v);
  }
  function num(v, min, max) {
    return typeof v === "number" && isFinite(v) && v >= min && v <= max;
  }

  /** The origin string of an absolute http(s) URL, or null. */
  function originOf(url) {
    try {
      var u = new URL(url);
      return u.protocol === "https:" || u.protocol === "http:" ? u.origin : null;
    } catch (e) {
      return null;
    }
  }

  function allowedParent(origin) {
    if (typeof origin !== "string" || originOf(origin) !== origin) return false;
    if (ALLOWED_PARENTS.indexOf(origin) !== -1) return true;
    var u = new URL(origin);
    return LOOPBACK.indexOf(u.hostname) !== -1;
  }

  /**
   * The parent origin this page is willing to serve: the `?parent=` value, only when it is on the allow-list AND it
   * is the page that actually embeds this one (ancestorOrigins where the browser has it, else the referrer).
   */
  function trustedParent(search, ancestorOrigins, referrer) {
    var wanted = new URLSearchParams(search).get("parent");
    if (!wanted || !allowedParent(wanted)) return null;
    var actual = null;
    if (ancestorOrigins && ancestorOrigins.length > 0) actual = ancestorOrigins[0];
    else if (referrer) actual = originOf(referrer);
    return actual === wanted ? wanted : null;
  }

  function parseToFrame(data) {
    if (!isObj(data) || data.ns !== NS) return null;
    if (data.type === "init") {
      if (typeof data.videoId !== "string" || !VIDEO_ID.test(data.videoId)) return null;
      if (typeof data.parentOrigin !== "string" || data.parentOrigin.length > 200) return null;
      if (data.start !== null && !num(data.start, 0, MAX_SECONDS)) return null;
      return { ns: NS, type: "init", videoId: data.videoId, start: data.start, parentOrigin: data.parentOrigin };
    }
    if (data.type !== "cmd") return null;
    if (data.cmd === "play" || data.cmd === "pause") return { ns: NS, type: "cmd", cmd: data.cmd };
    if (data.cmd === "seek" && num(data.value, 0, MAX_SECONDS)) return { ns: NS, type: "cmd", cmd: "seek", value: data.value };
    if (data.cmd === "rate" && num(data.value, 0.0625, 16)) return { ns: NS, type: "cmd", cmd: "rate", value: data.value };
    return null;
  }

  var core = { NS: NS, allowedParent: allowedParent, trustedParent: trustedParent, parseToFrame: parseToFrame, originOf: originOf };
  if (typeof globalThis !== "undefined") globalThis.NaadiPlayerCore = core;
  if (globalThis.__NAADI_PLAYER_NO_AUTORUN__) return;

  var parentOrigin = trustedParent(
    location.search,
    location.ancestorOrigins ? Array.prototype.slice.call(location.ancestorOrigins) : null,
    document.referrer,
  );
  if (!parentOrigin) return; // not embedded by Naadi: stay blank and silent

  var player = null;
  var started = false;

  function send(msg) {
    window.parent.postMessage(msg, parentOrigin);
  }

  function loadApi() {
    return new Promise(function (resolve, reject) {
      var timer = setTimeout(function () {
        reject(new Error("timeout"));
      }, API_TIMEOUT_MS);
      window.onYouTubeIframeAPIReady = function () {
        clearTimeout(timer);
        if (window.YT && window.YT.Player) resolve(window.YT);
        else reject(new Error("no player"));
      };
      var script = document.createElement("script");
      script.src = API;
      script.async = true;
      script.onerror = function () {
        reject(new Error("load"));
      };
      document.head.appendChild(script);
    });
  }

  function start(msg) {
    loadApi().then(
      function (YT) {
        var vars = { rel: 0, playsinline: 1, controls: 1, origin: location.origin };
        if (msg.start !== null && msg.start > 0) vars.start = Math.floor(msg.start);
        var p = new YT.Player("player", {
          videoId: msg.videoId,
          host: HOST,
          width: "100%",
          height: "100%",
          playerVars: vars,
          events: {
            onReady: function () {
              player = p;
              var rates = [1];
              var rate = 1;
              try {
                rates = p.getAvailablePlaybackRates();
                rate = p.getPlaybackRate();
              } catch (e) {
                /* keep the defaults */
              }
              send({ ns: NS, type: "ready", rates: rates, rate: rate });
              p.playVideo();
              setInterval(function () {
                try {
                  send({ ns: NS, type: "time", t: Math.max(0, p.getCurrentTime()), state: p.getPlayerState() });
                } catch (e) {
                  /* the player is gone */
                }
              }, TICK_MS);
            },
            onStateChange: function (e) {
              send({ ns: NS, type: "state", state: e.data });
            },
            onError: function (e) {
              send({ ns: NS, type: "error", code: e.data });
            },
          },
        });
      },
      function () {
        send({ ns: NS, type: "api_failed" });
      },
    );
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent || event.origin !== parentOrigin) return;
    var msg = parseToFrame(event.data);
    if (!msg) return;
    if (msg.type === "init") {
      if (started || msg.parentOrigin !== parentOrigin) return;
      started = true;
      start(msg);
      return;
    }
    if (!player) return;
    if (msg.cmd === "play") player.playVideo();
    else if (msg.cmd === "pause") player.pauseVideo();
    else if (msg.cmd === "seek") player.seekTo(msg.value, true);
    else player.setPlaybackRate(msg.value);
  });

  send({ ns: NS, type: "hello" });
})();
