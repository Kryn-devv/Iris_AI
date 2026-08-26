/* IRIS scene — Tier 1: the orb.
 *
 * A wireframe icosahedron whose surface is continuously displaced by layered
 * drifting noise, shaded with a fresnel rim glow so the edges facing away from
 * the camera burn brighter than the middle. That rim-lit falloff is what makes
 * it read as a translucent field of energy rather than a solid ball, and it is
 * why the material is additive with depth writing off: the far side of the mesh
 * shows through the near side and the two accumulate.
 *
 * Everything is driven by uniforms rather than by rebuilding geometry, so a
 * mood change is a number easing toward another number — never a rebuild, never
 * a snap.
 *
 * Exposes window.IrisOrb.create(opts) -> { group, update, setTargets, dispose }
 */
(function (global) {
  "use strict";

  var THREE = global.THREE;

  /* Ashima's simplex noise, the compact GLSL version. Used for the surface
   * displacement: three octaves at different scales and drift directions, so
   * the ripple never repeats visibly. */
  var NOISE_GLSL = [
    "vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}",
    "vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}",
    "float snoise(vec3 v){",
    "  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);",
    "  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);",
    "  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;",
    "  vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);",
    "  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;",
    "  i=mod289(i);",
    "  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))",
    "        +i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));",
    "  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;",
    "  vec4 j=p-49.0*floor(p*ns.z*ns.z);",
    "  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);",
    "  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);",
    "  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);",
    "  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0;",
    "  vec4 sh=-step(h,vec4(0.0));",
    "  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;",
    "  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y);",
    "  vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);",
    "  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));",
    "  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;",
    "  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);",
    "  m=m*m;",
    "  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));",
    "}",
  ].join("\n");

  /* Shared by the mesh and the halo: the displacement has to agree between them
   * or the halo detaches from the surface it is supposed to be hugging. */
  var DISPLACE_GLSL = [
    "uniform float uTime;",
    "uniform float uChurn;      // how fast the surface boils",
    "uniform float uAmp;        // displacement depth",
    "uniform float uLevel;      // live voice amplitude, 0..1",
    "uniform float uBass;       // low-band amplitude, 0..1",
    "float displacement(vec3 p, vec3 n){",
    "  float t = uTime * uChurn;",
    "  // Three octaves drifting in different directions so the ripple never",
    "  // settles into a visible loop.",
    "  float a = snoise(p * 1.15 + vec3(0.0, t * 0.55, 0.0));",
    "  float b = snoise(p * 2.60 + vec3(t * 0.42, 0.0, t * -0.30)) * 0.55;",
    "  float c = snoise(p * 5.20 + vec3(-t * 0.28, t * 0.36, t * 0.22)) * 0.26;",
    "  float base = (a + b + c) * 0.62;",
    "  // Voice rides on top: overall loudness deepens every ripple, bass adds a",
    "  // slower, broader swell so a low voice moves the whole surface.",
    "  float voice = base * uLevel * 1.35 + snoise(p * 0.85 + vec3(t * 0.2)) * uBass * 0.55;",
    "  return (base + voice) * uAmp;",
    "}",
  ].join("\n");

  var VERT = [
    NOISE_GLSL,
    DISPLACE_GLSL,
    "varying float vRim;",
    "varying float vDisp;",
    "void main(){",
    "  vec3 n = normalize(normal);",
    "  float d = displacement(position, n);",
    "  vec3 displaced = position + n * d;",
    "  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);",
    "  // Fresnel in view space: 1 at the silhouette, 0 facing the camera.",
    "  vec3 vn = normalize(normalMatrix * n);",
    "  vec3 viewDir = normalize(-mv.xyz);",
    "  vRim = 1.0 - abs(dot(vn, viewDir));",
    "  vDisp = d;",
    "  gl_Position = projectionMatrix * mv;",
    "}",
  ].join("\n");

  var FRAG = [
    "uniform vec3 uCore;",
    "uniform vec3 uRim;",
    "uniform float uBrightness;",
    "uniform float uRimPower;",
    "uniform float uOpacity;",
    "varying float vRim;",
    "varying float vDisp;",
    "void main(){",
    "  float rim = pow(clamp(vRim, 0.0, 1.0), uRimPower);",
    "  // Ridges catch a little extra light, which is what stops a displaced",
    "  // wireframe reading as flat noise.",
    "  float ridge = clamp(vDisp * 3.2, -0.4, 0.9);",
    "  vec3 col = mix(uCore, uRim, rim) * (0.30 + rim * 1.55 + ridge * 0.35);",
    "  float alpha = uOpacity * (0.30 + rim * 0.92);",
    "  gl_FragColor = vec4(col * uBrightness, alpha);",
    "}",
  ].join("\n");

  /* The halo is a slightly larger shell drawn from the inside, so what the
   * camera sees is its BACK faces — the softest possible edge, brightest at the
   * silhouette and fading to nothing in the middle. A sprite or a bloom pass
   * would both be heavier and less attached to the orb's actual shape. */
  var HALO_VERT = [
    "varying float vRim;",
    "void main(){",
    "  vec4 mv = modelViewMatrix * vec4(position, 1.0);",
    "  vec3 vn = normalize(normalMatrix * normalize(normal));",
    "  vRim = 1.0 - abs(dot(vn, normalize(-mv.xyz)));",
    "  gl_Position = projectionMatrix * mv;",
    "}",
  ].join("\n");

  var HALO_FRAG = [
    "uniform vec3 uColor;",
    "uniform float uStrength;",
    "varying float vRim;",
    "void main(){",
    "  float g = pow(clamp(1.0 - vRim, 0.0, 1.0), 3.4);",
    "  gl_FragColor = vec4(uColor * g * uStrength, g * uStrength * 0.55);",
    "}",
  ].join("\n");

  /* Ease every value toward its target by the same fraction of the remaining
   * distance each frame. One shared rate is what makes the whole scene share a
   * rhythm instead of each part moving to its own clock. Frame-rate corrected,
   * so a 30 fps device settles in the same wall-clock time as a 120 fps one. */
  function ease(current, target, rate, dt) {
    var k = 1.0 - Math.pow(1.0 - rate, dt * 60.0);
    return current + (target - current) * k;
  }

  /* Fine enough that the noise displacement reads as a smooth ripple rather
 * than a faceted silhouette. At detail 4 the outline was visibly a polygon. */
/* three.js IcosahedronGeometry(r, detail) gives 20*(detail+1)^2 faces, so
 * these are 1620 / 980 / 500 — dense enough that the noise reads as a smooth
 * ripple, sparse enough that the wireframe still shows its geodesic character
 * instead of turning into a solid haze. Line rendering, so it is cheap. */
var DETAIL = { high: 8, medium: 6, low: 4 };

  function create(opts) {
    opts = opts || {};
    var accent = new THREE.Color(opts.accent || "#5eead4");
    var detail = DETAIL[opts.quality] != null ? DETAIL[opts.quality] : DETAIL.high;

    var group = new THREE.Group();

    var geo = new THREE.IcosahedronGeometry(1, detail);
    var uniforms = {
      uTime: { value: 0 },
      uChurn: { value: 0.30 },
      uAmp: { value: 0.085 },
      uLevel: { value: 0 },
      uBass: { value: 0 },
      uCore: { value: accent.clone().multiplyScalar(0.35) },
      uRim: { value: accent.clone() },
      uBrightness: { value: 0.80 },
      uRimPower: { value: 1.9 },
      uOpacity: { value: 0.46 },
    };
    var mat = new THREE.ShaderMaterial({
      uniforms: uniforms,
      vertexShader: VERT,
      fragmentShader: FRAG,
      wireframe: true,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    var mesh = new THREE.Mesh(geo, mat);
    group.add(mesh);

    var haloGeo = new THREE.SphereGeometry(1.55, 48, 32);
    var haloUniforms = {
      uColor: { value: accent.clone() },
      uStrength: { value: 0.85 },
    };
    var haloMat = new THREE.ShaderMaterial({
      uniforms: haloUniforms,
      vertexShader: HALO_VERT,
      fragmentShader: HALO_FRAG,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
      side: THREE.BackSide,
    });
    var halo = new THREE.Mesh(haloGeo, haloMat);
    group.add(halo);

    /* Live values and the targets they ease toward. Tier 3 writes the targets;
     * Tier 1 just breathes. */
    var live = {
      churn: 0.30, amp: 0.085, brightness: 0.80, opacity: 0.46,
      haloStrength: 0.85, rimPower: 1.9, spin: 1.0, scale: 1.0,
      level: 0, bass: 0,
    };
    var target = Object.assign({}, live);
    var coreColor = accent.clone().multiplyScalar(0.35);
    var rimColor = accent.clone();
    var haloColor = accent.clone();
    var targetCore = coreColor.clone();
    var targetRim = rimColor.clone();
    var targetHalo = haloColor.clone();

    var spinY = 0, spinX = 0;

    function setTargets(next) {
      for (var key in next) {
        if (key === "core") { targetCore.set(next.core); continue; }
        if (key === "rim") { targetRim.set(next.rim); continue; }
        if (key === "halo") { targetHalo.set(next.halo); continue; }
        if (target[key] !== undefined) target[key] = next[key];
      }
    }

    /* Amplitude is smoothed asymmetrically — it leaps up on a loud syllable and
     * eases back down — so the orb springs to life on speech instead of
     * twitching frame to frame with the raw signal. */
    function pushAudio(level, bass) {
      target.level = level;
      target.bass = bass;
    }

    function update(dt, t, motionScale) {
      motionScale = motionScale == null ? 1 : motionScale;

      var attack = 0.42, decay = 0.055;
      live.level = ease(live.level, target.level,
                        target.level > live.level ? attack : decay, dt);
      live.bass = ease(live.bass, target.bass,
                       target.bass > live.bass ? attack * 0.8 : decay * 0.8, dt);

      live.churn = ease(live.churn, target.churn, 0.05, dt);
      live.amp = ease(live.amp, target.amp, 0.05, dt);
      live.brightness = ease(live.brightness, target.brightness, 0.05, dt);
      live.opacity = ease(live.opacity, target.opacity, 0.05, dt);
      live.haloStrength = ease(live.haloStrength, target.haloStrength, 0.05, dt);
      live.rimPower = ease(live.rimPower, target.rimPower, 0.05, dt);
      live.spin = ease(live.spin, target.spin, 0.04, dt);
      live.scale = ease(live.scale, target.scale, 0.06, dt);

      coreColor.lerp(targetCore, 1.0 - Math.pow(1.0 - 0.045, dt * 60.0));
      rimColor.lerp(targetRim, 1.0 - Math.pow(1.0 - 0.045, dt * 60.0));
      haloColor.lerp(targetHalo, 1.0 - Math.pow(1.0 - 0.045, dt * 60.0));

      uniforms.uTime.value = t;
      /* Churn is continuous surface movement, so it belongs under the motion
       * scale too — holding the agents still while the orb keeps boiling is
       * not what "reduce motion" means. */
      uniforms.uChurn.value = live.churn * (0.25 + motionScale * 0.75);
      uniforms.uAmp.value = live.amp;
      uniforms.uLevel.value = live.level;
      uniforms.uBass.value = live.bass;
      uniforms.uBrightness.value = live.brightness;
      uniforms.uRimPower.value = live.rimPower;
      uniforms.uOpacity.value = live.opacity;
      uniforms.uCore.value.copy(coreColor);
      uniforms.uRim.value.copy(rimColor);
      haloUniforms.uColor.value.copy(haloColor);
      haloUniforms.uStrength.value = live.haloStrength;

      /* Two axes at once, mostly around the vertical with a slight tilt, so it
       * tumbles rather than spinning like a globe on a stand. */
      spinY += dt * 0.115 * live.spin * motionScale;
      spinX += dt * 0.031 * live.spin * motionScale;
      group.rotation.y = spinY;
      group.rotation.x = Math.sin(spinX) * 0.22;

      /* Breathing: always present, so it is never a static ball. The voice
       * pulse adds to it rather than replacing it. */
      var breathe = 1.0 + Math.sin(t * 1.05) * 0.016 * motionScale;
      var s = live.scale * breathe * (1.0 + live.level * 0.05);
      mesh.scale.setScalar(s);
      halo.scale.setScalar(s);
    }

    function dispose() {
      geo.dispose(); mat.dispose();
      haloGeo.dispose(); haloMat.dispose();
    }

    return {
      group: group,
      mesh: mesh,
      halo: halo,
      update: update,
      setTargets: setTargets,
      pushAudio: pushAudio,
      colorNow: function () { return rimColor; },
      levelNow: function () { return live.level; },
      dispose: dispose,
    };
  }

  global.IrisOrb = { create: create, ease: ease, NOISE_GLSL: NOISE_GLSL };
})(window);
