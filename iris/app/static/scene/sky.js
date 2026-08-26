/* IRIS scene — Tier 2: the cosmic background.
 *
 * Two things drawn one after the other into their OWN renderer, separate from
 * the orb's. That separation is the whole point: this layer is a full-screen
 * fragment shader and by far the most expensive thing on the page, but it also
 * drifts so slowly that rendering it at half resolution and every other frame
 * is invisible. Keeping it on its own renderer means the orb stays crisp while
 * this one quietly degrades on a weak machine.
 *
 *   1. THE SKY   — a procedural backdrop: dark base brighter in the middle,
 *                  several nebula cloud layers drifting in different directions
 *                  and colours, two star layers twinkling on their own rhythms,
 *                  a soft glow behind the orb that picks up the orb's LIVE
 *                  colour so the orb appears to light the space behind it.
 *   2. THE WEB   — a faint 3D lattice floating far behind: ~100 nodes gathered
 *                  into 8 coloured clusters, thin lines between near pairs, and
 *                  a few hundred dust motes. Dim and additive so it reads as
 *                  depth rather than clutter.
 *
 * Exposes window.IrisSky.create(opts)
 */
(function (global) {
  "use strict";

  var THREE = global.THREE;

  /* ─────────────────────────── the sky ─────────────────────────── */

  var SKY_VERT = [
    "varying vec2 vUv;",
    "void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }",
  ].join("\n");

  /* 2D simplex — cheaper than the 3D version and the clouds only need to drift
   * in a plane. fbm over five octaves gives the wispy, self-similar structure
   * that reads as gas rather than as blobs. */
  var SKY_NOISE = [
    "vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec2 mod289(vec2 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec3 permute(vec3 x){return mod289(((x*34.0)+1.0)*x);}",
    "float snoise2(vec2 v){",
    "  const vec4 C=vec4(0.211324865405187,0.366025403784439,-0.577350269189626,0.024390243902439);",
    "  vec2 i=floor(v+dot(v,C.yy)); vec2 x0=v-i+dot(i,C.xx);",
    "  vec2 i1=(x0.x>x0.y)?vec2(1.0,0.0):vec2(0.0,1.0);",
    "  vec4 x12=x0.xyxy+C.xxzz; x12.xy-=i1;",
    "  i=mod289(i);",
    "  vec3 p=permute(permute(i.y+vec3(0.0,i1.y,1.0))+i.x+vec3(0.0,i1.x,1.0));",
    "  vec3 m=max(0.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.0);",
    "  m=m*m; m=m*m;",
    "  vec3 x=2.0*fract(p*C.www)-1.0; vec3 h=abs(x)-0.5;",
    "  vec3 ox=floor(x+0.5); vec3 a0=x-ox;",
    "  m*=1.79284291400159-0.85373472095314*(a0*a0+h*h);",
    "  vec3 g; g.x=a0.x*x0.x+h.x*x0.y;",
    "  g.yz=a0.yz*x12.xz+h.yz*x12.yw;",
    "  return 130.0*dot(m,g);",
    "}",
    "float fbm(vec2 p){",
    "  float v=0.0, a=0.5;",
    "  for(int i=0;i<5;i++){ v += a*snoise2(p); p = p*2.03 + 17.1; a *= 0.5; }",
    "  return v;",
    "}",
    /* Cheap 2D hash for the star grids. */
    "float hash21(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }",
  ].join("\n");

  var SKY_FRAG = [
    "precision highp float;",
    SKY_NOISE,
    "uniform float uTime;",
    "uniform vec2  uRes;",
    "uniform vec3  uOrbColor;",
    "uniform float uOrbGlow;",
    "uniform float uFade;",
    "uniform float uMotion;",
    "uniform vec2  uOrbUv;",
    "varying vec2 vUv;",
    "",
    /* One star layer. Each cell of a grid either holds a star or does not, and
     * the ones that do twinkle on a rhythm derived from their own hash — so no
     * two blink together, which is what stops a starfield looking like a
     * texture. */
    "float starLayer(vec2 uv, float density, float size, float seed, float t){",
    "  vec2 g = uv * density;",
    "  vec2 id = floor(g);",
    "  vec2 gv = fract(g) - 0.5;",
    "  float h = hash21(id + seed);",
    "  if (h < 0.90) return 0.0;",
    "  vec2 off = vec2(hash21(id + seed + 11.0), hash21(id + seed + 23.0)) - 0.5;",
    "  float d = length(gv - off * 0.72);",
    "  float tw = 0.45 + 0.55 * sin(t * (0.6 + h * 2.4) + h * 40.0);",
    "  float core = smoothstep(size, 0.0, d);",
    "  return core * tw * (0.35 + h * 0.65);",
    "}",
    "",
    "void main(){",
    "  vec2 uv = vUv;",
    "  float aspect = uRes.x / max(uRes.y, 1.0);",
    "  vec2 p = (uv - 0.5) * vec2(aspect, 1.0);",
    "  float r = length(p);",
    "  float t = uTime * uMotion;",
    "",
    "  // Base: a dark blue void, a touch brighter in the middle so the eye",
    "  // settles centre-screen where the orb is.",
    "  vec3 col = vec3(0.008, 0.012, 0.030) + vec3(0.010, 0.020, 0.042) * (1.0 - smoothstep(0.0, 0.95, r));",
    "",
    "  // Three nebula layers, each its own colour, scale and drift direction.",
    "  // Squaring the mask keeps the wisps thin instead of filling the frame.",
    "  vec2 q1 = p * 1.30 + vec2(t * 0.0130, t * -0.0075);",
    "  float n1 = fbm(q1);",
    "  float m1 = pow(smoothstep(0.05, 1.15, n1 + 0.42), 2.0);",
    "  col += vec3(0.045, 0.155, 0.140) * m1 * 0.80;",
    "",
    "  vec2 q2 = p * 0.85 + vec2(t * -0.0092, t * 0.0121) + 31.7;",
    "  float n2 = fbm(q2);",
    "  float m2 = pow(smoothstep(0.15, 1.25, n2 + 0.34), 2.4);",
    "  col += vec3(0.180, 0.055, 0.225) * m2 * 0.95;",
    "",
    "  vec2 q3 = p * 2.10 + vec2(t * 0.0066, t * 0.0098) + 77.3;",
    "  float n3 = fbm(q3);",
    "  float m3 = pow(smoothstep(0.25, 1.30, n3 + 0.28), 2.8);",
    "  col += vec3(0.048, 0.100, 0.260) * m3 * 0.85;",
    "",
    "  // Stars: one dense fine layer, one sparse larger layer.",
    "  vec2 su = uv * vec2(aspect, 1.0);",
    "  // Twinkle on MOTION-scaled time: under prefers-reduced-motion the whole",
    "  // field is the loudest thing on screen if it keeps flickering.",
    "  float s1 = starLayer(su, 190.0, 0.055, 3.0, t);",
    "  float s2 = starLayer(su, 78.0, 0.085, 91.0, t * 0.7);",
    "  col += vec3(0.72, 0.83, 1.00) * s1 * 0.72;",
    "  col += vec3(0.86, 0.94, 1.00) * s2 * 0.95;",
    "",
    "  // The orb's own light pooling on the space behind it. Tracks the orb's",
    "  // live colour, so the whole scene shifts mood with it.",
    "  vec2 op = (uOrbUv - 0.5) * vec2(aspect, 1.0);",
    "  float od = length(p - op);",
    "  float pool = exp(-od * od * 8.5) * (0.10 + uOrbGlow * 0.46);",
    "  col += uOrbColor * pool;",
    "  col += uOrbColor * exp(-od * od * 40.0) * (0.04 + uOrbGlow * 0.20);",
    "",
    "  // Corner falloff, so the eye stays centred.",
    "  col *= 1.0 - 0.55 * smoothstep(0.35, 1.05, r);",
    "",
    "  gl_FragColor = vec4(col * uFade, 1.0);",
    "}",
  ].join("\n");

  /* ───────────────────── the distant network ───────────────────── */

  function buildWeb(opts) {
    var group = new THREE.Group();
    var mobile = !!opts.mobile;
    var nodeCount = mobile ? 52 : 104;
    var dustCount = mobile ? 160 : 340;
    var clusterCount = 8;

    var palette = [
      new THREE.Color(0x5eead4), new THREE.Color(0x67e8f9),
      new THREE.Color(0x818cf8), new THREE.Color(0xa78bfa),
      new THREE.Color(0x38bdf8), new THREE.Color(0x2dd4bf),
      new THREE.Color(0x8b5cf6), new THREE.Color(0x22d3ee),
    ];

    /* Clusters rather than a uniform cloud: an even scatter reads as noise,
     * whereas clumps read as structure at a distance. */
    var centres = [];
    for (var c = 0; c < clusterCount; c++) {
      var a = (c / clusterCount) * Math.PI * 2 + 0.6;
      var rad = 7.4 + Math.sin(c * 2.3) * 2.6;
      centres.push(new THREE.Vector3(
        Math.cos(a) * rad,
        Math.sin(a * 1.7) * 4.2,
        -14.0 + Math.sin(c * 1.3) * 5.0
      ));
    }

    var positions = new Float32Array(nodeCount * 3);
    var colors = new Float32Array(nodeCount * 3);
    var sizes = new Float32Array(nodeCount);
    var points = [];
    for (var i = 0; i < nodeCount; i++) {
      var ci = i % clusterCount;
      var centre = centres[ci];
      var v = new THREE.Vector3(
        centre.x + (Math.random() - 0.5) * 3.0,
        centre.y + (Math.random() - 0.5) * 2.4,
        centre.z + (Math.random() - 0.5) * 2.6
      );
      points.push({ v: v, ci: ci });
      positions[i * 3] = v.x; positions[i * 3 + 1] = v.y; positions[i * 3 + 2] = v.z;
      var col = palette[ci];
      colors[i * 3] = col.r; colors[i * 3 + 1] = col.g; colors[i * 3 + 2] = col.b;
      sizes[i] = 0.045 + Math.random() * 0.060;
    }

    var nodeGeo = new THREE.BufferGeometry();
    nodeGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    nodeGeo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    nodeGeo.setAttribute("aSize", new THREE.BufferAttribute(sizes, 1));

    var nodeMat = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uFade: { value: 0 } },
      vertexShader: [
        "attribute float aSize;",
        "varying vec3 vColor;",
        "varying float vTw;",
        "uniform float uTime;",
        "void main(){",
        "  vColor = color;",
        "  vec4 mv = modelViewMatrix * vec4(position, 1.0);",
        "  vTw = 0.55 + 0.45 * sin(uTime * 0.7 + position.x * 2.1 + position.y * 1.3);",
        "  gl_PointSize = aSize * 190.0 / max(-mv.z, 0.001);",
        "  gl_Position = projectionMatrix * mv;",
        "}",
      ].join("\n"),
      fragmentShader: [
        "varying vec3 vColor;",
        "varying float vTw;",
        "uniform float uFade;",
        "void main(){",
        "  float d = length(gl_PointCoord - 0.5);",
        "  float g = smoothstep(0.5, 0.0, d);",
        "  gl_FragColor = vec4(vColor * g * vTw * 0.62, g * vTw * 0.34 * uFade);",
        "}",
      ].join("\n"),
      transparent: true,
      vertexColors: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    group.add(new THREE.Points(nodeGeo, nodeMat));

    /* Lines only between nodes that are genuinely close, so the lattice shows
     * the cluster structure instead of becoming a hairball. */
    var linePos = [];
    var lineCol = [];
    var LINK = 2.3;
    for (var a1 = 0; a1 < points.length; a1++) {
      for (var b1 = a1 + 1; b1 < points.length; b1++) {
        if (points[a1].v.distanceTo(points[b1].v) > LINK) continue;
        linePos.push(points[a1].v.x, points[a1].v.y, points[a1].v.z,
                     points[b1].v.x, points[b1].v.y, points[b1].v.z);
        var ca = palette[points[a1].ci], cb = palette[points[b1].ci];
        lineCol.push(ca.r, ca.g, ca.b, cb.r, cb.g, cb.b);
      }
    }
    var lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute("position", new THREE.Float32BufferAttribute(linePos, 3));
    lineGeo.setAttribute("color", new THREE.Float32BufferAttribute(lineCol, 3));
    var lineMat = new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    var lines = new THREE.LineSegments(lineGeo, lineMat);
    group.add(lines);

    /* Dust: no structure at all, just parallax. */
    var dustPos = new Float32Array(dustCount * 3);
    for (var d2 = 0; d2 < dustCount; d2++) {
      dustPos[d2 * 3] = (Math.random() - 0.5) * 26;
      dustPos[d2 * 3 + 1] = (Math.random() - 0.5) * 16;
      dustPos[d2 * 3 + 2] = -3 - Math.random() * 18;
    }
    var dustGeo = new THREE.BufferGeometry();
    dustGeo.setAttribute("position", new THREE.BufferAttribute(dustPos, 3));
    var dustMat = new THREE.PointsMaterial({
      color: 0x94a3c7, size: 0.035, sizeAttenuation: true,
      transparent: true, opacity: 0, blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    group.add(new THREE.Points(dustGeo, dustMat));

    return {
      group: group,
      nodeMat: nodeMat,
      lineMat: lineMat,
      dustMat: dustMat,
      dispose: function () {
        nodeGeo.dispose(); nodeMat.dispose();
        lineGeo.dispose(); lineMat.dispose();
        dustGeo.dispose(); dustMat.dispose();
      },
    };
  }

  /* ─────────────────────────── the layer ─────────────────────────── */

  function create(opts) {
    opts = opts || {};
    var accent = new THREE.Color(opts.accent || "#5eead4");

    var scene = new THREE.Scene();

    /* The sky is a single triangle-pair in clip space — no camera transform, so
     * it always covers the viewport exactly regardless of aspect. */
    var skyUniforms = {
      uTime: { value: 0 },
      uRes: { value: new THREE.Vector2(1, 1) },
      uOrbColor: { value: accent.clone() },
      uOrbGlow: { value: 0.0 },
      uFade: { value: 0.0 },
      uMotion: { value: 1.0 },
      uOrbUv: { value: new THREE.Vector2(0.5, 0.5) },
    };
    var skyMat = new THREE.ShaderMaterial({
      uniforms: skyUniforms,
      vertexShader: SKY_VERT,
      fragmentShader: SKY_FRAG,
      depthWrite: false,
      depthTest: false,
    });
    var skyQuad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), skyMat);
    skyQuad.frustumCulled = false;
    scene.add(skyQuad);

    var web = buildWeb(opts);
    scene.add(web.group);

    var camera = new THREE.PerspectiveCamera(50, 1, 0.1, 400);
    camera.position.set(0, 0, 6);

    var fade = 0;
    var drift = { x: 0, y: 0 };

    function resize(w, h) {
      skyUniforms.uRes.value.set(w, h);
      camera.aspect = w / Math.max(h, 1);
      camera.updateProjectionMatrix();
    }

    function update(dt, t, params) {
      params = params || {};
      var motion = params.motionScale == null ? 1 : params.motionScale;

      /* Fade in over ~1.5 s so the field does not pop in on load. */
      fade = Math.min(1, fade + dt / 1.5);
      skyUniforms.uFade.value = fade;
      web.nodeMat.uniforms.uFade.value = fade;
      web.lineMat.opacity = 0.040 * fade;
      web.dustMat.opacity = 0.22 * fade;

      skyUniforms.uTime.value = t;
      skyUniforms.uMotion.value = motion;
      if (params.orbColor) skyUniforms.uOrbColor.value.copy(params.orbColor);
      if (params.orbGlow != null) {
        skyUniforms.uOrbGlow.value +=
          (params.orbGlow - skyUniforms.uOrbGlow.value) * (1 - Math.pow(1 - 0.05, dt * 60));
      }
      if (params.orbUv) skyUniforms.uOrbUv.value.copy(params.orbUv);

      web.nodeMat.uniforms.uTime.value = t;

      /* The whole lattice turns as one, very slowly. */
      web.group.rotation.y = t * 0.011 * motion;
      web.group.rotation.x = Math.sin(t * 0.037) * 0.06 * motion;

      /* A wandering camera makes the field feel inhabited without ever being
       * distracting — two slow incommensurate sines, so it never repeats. */
      drift.x = Math.sin(t * 0.061) * 0.42 + Math.sin(t * 0.023) * 0.22;
      drift.y = Math.cos(t * 0.049) * 0.28 + Math.sin(t * 0.017) * 0.14;
      camera.position.x = drift.x * motion;
      camera.position.y = drift.y * motion;
      camera.lookAt(0, 0, -6);
    }

    return {
      scene: scene,
      camera: camera,
      resize: resize,
      update: update,
      dispose: function () {
        skyQuad.geometry.dispose(); skyMat.dispose(); web.dispose();
      },
    };
  }

  global.IrisSky = { create: create };
})(window);
