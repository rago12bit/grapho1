// The hero cube: the graph's schema as 27 pieces. Drag (or swipe sideways) to turn it; click to take it apart,
// then hover or tap a piece to see which node label it is, what it holds, and how many there are right now.
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export function mountCube(canvas, pieces, { onPick, onOpen } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.25;
  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
  camera.position.set(0, 0.4, 12.5);
  const key = new THREE.DirectionalLight(0xffffff, 1.4);
  key.position.set(4, 6, 5);
  scene.add(key);
  const rim = new THREE.PointLight(0xff6b4a, 18, 20);
  rim.position.set(-4, -2, 3);
  scene.add(rim);

  const group = new THREE.Group();
  group.rotation.set(0.52, -0.72, 0);
  scene.add(group);
  const geo = new RoundedBoxGeometry(0.92, 0.92, 0.92, 4, 0.12);
  const base = () => new THREE.MeshPhysicalMaterial({ color: 0x16161a, metalness: 0.55, roughness: 0.24, clearcoat: 1, clearcoatRoughness: 0.05,
    envMapIntensity: 1.9, emissive: 0xff6b4a, emissiveIntensity: 0 });
  const meshes = [];
  let i = 0;
  for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
    const m = new THREE.Mesh(geo, base());
    const home = new THREE.Vector3(x * 1.0, y * 1.0, z * 1.0);
    const dir = home.lengthSq() ? home.clone().normalize() : new THREE.Vector3(0, 0, 0);
    const far = home.clone().multiplyScalar(2.1).add(dir.clone().multiplyScalar(0.35 + Math.random() * 0.4))
      .add(new THREE.Vector3((Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.5, (Math.random() - 0.5) * 0.5));
    const spin = new THREE.Euler((Math.random() - 0.5) * 1.6, (Math.random() - 0.5) * 1.6, (Math.random() - 0.5) * 1.6);
    m.position.copy(home);
    m.userData = { home, far, spin, piece: pieces[i] || pieces[pieces.length - 1], i };
    if (m.userData.piece.core) m.material.emissiveIntensity = 0.0;
    group.add(m);
    meshes.push(m);
    i++;
  }

  // state
  let open = false, k = 0, from = 0, t0 = 0;               // k: 0 assembled .. 1 apart
  let velX = 0, velY = 0.0035, down = null, hovered = null, pinned = null;
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();

  let baseZ = 12;
  const resize = () => {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    baseZ = w / h < 0.9 ? 14 : 12;                              // narrow screens: step back a little
    camera.updateProjectionMatrix();
  };
  new ResizeObserver(resize).observe(canvas);
  resize();

  const pick = (e) => {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(meshes, false)[0];
    return hit ? hit.object : null;
  };
  const glow = (m, on) => { if (m) m.material.emissiveIntensity = on ? 0.55 : m.userData.piece.core && open ? 0.35 : 0; };
  const report = (m, e) => {
    if (!onPick) return;
    if (!m) return onPick(null);
    const r = canvas.getBoundingClientRect();
    onPick(m.userData.piece, e.clientX - r.left, e.clientY - r.top);
  };
  const toggle = () => {
    open = !open; from = k; t0 = performance.now();
    glow(pinned, false); pinned = null; report(null);
    meshes.forEach((m) => glow(m, false));
    onOpen && onOpen(open);
  };

  canvas.addEventListener("pointerdown", (e) => {
    down = { x: e.clientX, y: e.clientY, t: performance.now(), lx: e.clientX, ly: e.clientY, type: e.pointerType };
    if (e.pointerType === "mouse") canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (down) {
      const dx = e.clientX - down.lx, dy = e.clientY - down.ly;
      down.lx = e.clientX; down.ly = e.clientY;
      velY = dx * 0.006;
      velX = down.type === "mouse" ? dy * 0.006 : 0;            // on touch, vertical swipes scroll the page
      return;
    }
    if (e.pointerType !== "mouse") return;
    const m = open ? pick(e) : null;
    if (m !== hovered) { if (hovered !== pinned) glow(hovered, false); hovered = m; glow(m, true); }
    if (!pinned) report(m, e);
    canvas.style.cursor = m ? "pointer" : "grab";
  });
  const up = (e) => {
    if (!down) return;
    const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y), quick = performance.now() - down.t < 450;
    down = null;
    if (moved > 6 || !quick) return;
    if (!open) return toggle();
    const m = pick(e);
    if (!m) return toggle();                                   // tap empty space: put it back together
    glow(pinned, false); pinned = m; glow(m, true); report(m, e);
  };
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", () => { down = null; });
  canvas.addEventListener("pointerleave", () => { if (!pinned) { glow(hovered, false); hovered = null; report(null); } });

  let visible = true;
  new IntersectionObserver(([x]) => { visible = x.isIntersecting; }).observe(canvas);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const tick = (now) => {
    requestAnimationFrame(tick);
    if (!visible) return;
    const dur = 950;
    const target = open ? 1 : 0;
    if (k !== target) {
      const p = Math.min(1, (now - t0) / dur);
      k = from + (target - from) * ease(p);
      if (p === 1) k = target;
    }
    for (const m of meshes) {
      const { home, far, spin } = m.userData;
      m.position.lerpVectors(home, far, k);
      m.rotation.set(spin.x * k, spin.y * k, spin.z * k);
      if (m.userData.piece.core) m.material.emissiveIntensity = m === hovered || m === pinned ? 0.55 : 0.35 * k;
    }
    if (!down) {
      velX *= 0.93;
      velY = velY * 0.95 + (reduce ? 0 : 0.0028) * 0.05;          // settle back to a slow idle turn
    }
    group.rotation.y += velY;
    group.rotation.x = Math.max(-1.2, Math.min(1.2, group.rotation.x + velX));
    group.position.y = reduce ? 0 : Math.sin(now / 1400) * 0.06;
    camera.position.z = baseZ + 6.5 * k;                      // step back as it comes apart, so every piece stays in view
    renderer.render(scene, camera);
  };
  requestAnimationFrame(tick);
  return { toggle, isOpen: () => open };
}
