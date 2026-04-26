import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import * as PIXI from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display';

window.PIXI = PIXI;

// Parameter recipes for named emotions. The Haru model ships no .exp3.json
// files, so we drive expressions by setting cdi3 parameter IDs directly.
//
// Important: NEVER include ParamEyeLOpen / ParamEyeROpen here — overriding
// those would freeze the engine's automatic blinking. Use Smile/Form/Brow
// params to convey eye-shape changes instead, so blinks stay alive.
const EXPRESSION_PRESETS = {
  neutral: {},
  happy: {
    ParamEyeLSmile: 1, ParamEyeRSmile: 1,
    ParamMouthForm: 1, ParamTere: 0.3,
  },
  excited: {
    ParamEyeLSmile: 1, ParamEyeRSmile: 1,
    ParamMouthForm: 1, ParamMouthOpenY: 0.5,
    ParamTere: 0.6,
    ParamBrowLY: 0.5, ParamBrowRY: 0.5,
  },
  sad: {
    ParamMouthForm: -1,
    ParamBrowLY: -0.5, ParamBrowRY: -0.5,
    ParamBrowLForm: -1, ParamBrowRForm: -1,
    ParamTear: 0.5,
  },
  surprised: {
    // Wide-eye look without locking eyelids — brows + mouth do the work.
    ParamBrowLY: 1, ParamBrowRY: 1,
    ParamEyeForm: 1,
    ParamMouthOpenY: 0.7,
  },
  angry: {
    ParamMouthForm: -0.5,
    ParamBrowLY: -0.7, ParamBrowRY: -0.7,
    ParamBrowLAngle: 1, ParamBrowRAngle: -1,
    ParamEyeLSmile: -0.3, ParamEyeRSmile: -0.3,
  },
};

// Time constant for the exponential easing (seconds). At ~0.18s, an
// expression converges ~99% in roughly 800ms — fast enough to feel
// responsive, slow enough to read as a transition rather than a snap.
const EXPRESSION_TAU = 0.18;
// Below this absolute value, drop the param entirely so the engine's
// default/idle animation can take over again.
const PARAM_RELEASE_EPSILON = 0.005;

// Procedural idle motion. We layer gentle multi-frequency sine waves on top
// of whatever motions are playing (via addParameterValueById, which is
// additive) so the model never looks frozen between facial expressions. Each
// emotion tints the idle: excited sways harder and faster, sad slumps and
// slows, etc. `angleYBias` is a sustained head tilt (positive = looking up).
const IDLE_PROFILES = {
  neutral:   { sway: 1.0,  speed: 1.0,  angleYBias: 0 },
  happy:     { sway: 1.1,  speed: 1.15, angleYBias: 1 },
  excited:   { sway: 1.5,  speed: 1.4,  angleYBias: 2 },
  sad:       { sway: 0.45, speed: 0.65, angleYBias: -5 },
  surprised: { sway: 0.7,  speed: 1.0,  angleYBias: 2 },
  angry:     { sway: 0.7,  speed: 1.2,  angleYBias: -1 },
};

const Live2DCharacter = forwardRef(function Live2DCharacter(
  { modelUrl, draggable = true, clickToPlayMotion = true, style },
  ref,
) {
  const containerRef = useRef(null);
  const appRef = useRef(null);
  const modelRef = useRef(null);
  // The animated current value of every param we're driving. Eased toward
  // `targetParamsRef` each frame.
  const currentParamsRef = useRef({});
  // The destination set by the latest setExpression() call.
  const targetParamsRef = useRef({});
  // Legacy direct-write map — kept for setExpressionParams users; layered on
  // top of the eased values, so callers who want raw control still get it.
  const expressionParamsRef = useRef({});
  const lastTickRef = useRef(0);
  // Eased idle-motion profile. `current` is what we actually render with this
  // frame; `target` is what setExpression set it to. Same exponential easing
  // as facial params so emotion+idle shift together.
  const currentIdleRef = useRef({ ...IDLE_PROFILES.neutral });
  const targetIdleRef = useRef({ ...IDLE_PROFILES.neutral });
  // Monotonic clock for the sine oscillators — captured on first tick.
  const idleStartRef = useRef(0);
  const motionGroupsRef = useRef([]);

  useImperativeHandle(ref, () => ({
    playMotion(group, index) {
      const model = modelRef.current;
      if (!model) return;
      model.motion(group, index);
    },
    playRandomMotion(group = '') {
      const model = modelRef.current;
      if (!model) return;
      const defs = model.internalModel.motionManager.definitions[group] || [];
      if (!defs.length) return;
      model.motion(group, Math.floor(Math.random() * defs.length));
    },
    setExpressionParams(params) {
      expressionParamsRef.current = { ...expressionParamsRef.current, ...params };
    },
    clearExpressionParams() {
      expressionParamsRef.current = {};
    },
    setExpression(name) {
      const preset = EXPRESSION_PRESETS[name];
      if (!preset) {
        console.warn(`[Live2D] setExpression: unknown name "${name}", falling back to neutral`);
      }
      const chosen = preset ?? EXPRESSION_PRESETS.neutral;
      console.log(`[Live2D] setExpression(${name})`, chosen);
      if (!modelRef.current) {
        console.warn('[Live2D] setExpression called before model loaded — buffering');
      }
      // Build target from the union of (currently-driven params, new preset).
      // Anything in current but not in the new preset eases back to 0 so old
      // expression bits don't linger.
      const target = {};
      const ids = new Set([
        ...Object.keys(currentParamsRef.current),
        ...Object.keys(chosen),
      ]);
      for (const id of ids) target[id] = chosen[id] ?? 0;
      targetParamsRef.current = target;
      // Match the idle-motion profile to the emotion so the body language
      // shifts with the face.
      targetIdleRef.current = IDLE_PROFILES[name] ?? IDLE_PROFILES.neutral;
    },
    getMotionGroups() {
      return motionGroupsRef.current;
    },
  }));

  useEffect(() => {
    let cancelled = false;

    const container = containerRef.current;
    // Let PIXI manage its own <canvas>. Reusing a canvas that already had
    // a WebGL context (which happens under React StrictMode's double-invoke
    // of effects) throws inside Renderer.create.
    const app = new PIXI.Application({
      autoStart: true,
      resizeTo: container,
      backgroundAlpha: 0,
    });
    appRef.current = app;
    const view = app.view;
    view.style.display = 'block';
    view.style.width = '100%';
    view.style.height = '100%';
    container.appendChild(view);

    (async () => {
      let model;
      try {
        model = await Live2DModel.from(modelUrl);
      } catch (err) {
        console.error(`[Live2DCharacter] failed to load model from ${modelUrl}:`, err);
        return;
      }
      if (cancelled) {
        model.destroy();
        return;
      }
      modelRef.current = model;
      app.stage.addChild(model);

      const fit = () => {
        const w = app.renderer.width;
        const h = app.renderer.height;
        // Pre-scale fill ratio: how much of the viewport the model's bounding
        // box may occupy. Lower = more zoomed out / more breathing room.
        const FILL = 0.5;
        const scale = Math.min((w * FILL) / model.width, (h * FILL) / model.height);
        model.scale.set(scale);
        model.x = (w - model.width) / 2;
        // Vertical bias: the moc3 bounding box has empty padding above/below
        // the character, so naive centering lands on the hips. Pulling y down
        // (positive offset) shifts the model toward the bottom of the canvas,
        // bringing the head/upper-body into the visual center.
        model.y = (h - model.height) / 2 + h * 0.12;
      };
      fit();
      const resizeObserver = new ResizeObserver(fit);
      resizeObserver.observe(container);
      app.fit = fit;
      app.resizeObserver = resizeObserver;

      motionGroupsRef.current = Object.keys(model.internalModel.motionManager.definitions || {});

      // Override params after the motion/eyeblink/physics/pose pass has run
      // AND after coreModel.update() has baked. We then re-bake so the render
      // sees our values. Wrapping internalModel.update is the only hook that
      // reliably runs in that window — `beforeModelUpdate` fires before
      // physics/pose, and `afterModelUpdate` fires after the render.
      const missingParams = new Set();
      let firstApplyLogged = false;
      const internalModel = model.internalModel;
      const originalUpdate = internalModel.update.bind(internalModel);
      internalModel.update = function patchedUpdate(...args) {
        const result = originalUpdate(...args);
        const core = internalModel.coreModel;

        // 1. Ease currentParams toward targetParams.
        const now = performance.now();
        const dt = lastTickRef.current === 0 ? 0.016 : (now - lastTickRef.current) / 1000;
        lastTickRef.current = now;
        const alpha = 1 - Math.exp(-dt / EXPRESSION_TAU);

        const current = currentParamsRef.current;
        const target = targetParamsRef.current;
        const lerpIds = new Set([...Object.keys(current), ...Object.keys(target)]);
        for (const id of lerpIds) {
          const c = current[id] ?? 0;
          const t = target[id] ?? 0;
          const next = c + (t - c) * alpha;
          if (Math.abs(next) < PARAM_RELEASE_EPSILON && Math.abs(t) < PARAM_RELEASE_EPSILON) {
            // Converged to ~0 — release so engine defaults / blinks resume.
            delete current[id];
            delete target[id];
            continue;
          }
          current[id] = next;
        }

        // 2. Layer eased values, then any direct setExpressionParams overrides.
        const directParams = expressionParamsRef.current;
        const writeIds = new Set([...Object.keys(current), ...Object.keys(directParams)]);
        for (const id of writeIds) {
          const value = directParams[id] ?? current[id];
          try {
            core.setParameterValueById(id, value);
          } catch (err) {
            if (!missingParams.has(id)) {
              missingParams.add(id);
              console.warn(`[Live2D] failed to set param "${id}":`, err);
            }
          }
        }

        // 3. Idle motion. Ease the profile, then ADD multi-frequency sines to
        //    head/body angles so the model is always alive even when no motion
        //    is playing. addParameterValueById layers on whatever motions
        //    wrote, instead of fighting them.
        const cIdle = currentIdleRef.current;
        const tIdle = targetIdleRef.current;
        cIdle.sway += (tIdle.sway - cIdle.sway) * alpha;
        cIdle.speed += (tIdle.speed - cIdle.speed) * alpha;
        cIdle.angleYBias += (tIdle.angleYBias - cIdle.angleYBias) * alpha;

        if (idleStartRef.current === 0) idleStartRef.current = now;
        const elapsed = (now - idleStartRef.current) / 1000;
        const sway = cIdle.sway;
        const w = cIdle.speed;
        // Each axis is the sum of two sines at irrational frequency ratios so
        // the wave never repeats exactly — reads as organic instead of robotic.
        const sin = Math.sin;
        const idle = [
          ['ParamAngleX',     sway * (4.0 * sin(elapsed * 0.80 * w + 0.0) + 2.0 * sin(elapsed * 1.37 * w + 0.6))],
          ['ParamAngleY',     sway * (2.0 * sin(elapsed * 0.65 * w + 0.7) + 1.2 * sin(elapsed * 0.93 * w + 1.4)) + cIdle.angleYBias],
          ['ParamAngleZ',     sway * (2.5 * sin(elapsed * 0.50 * w + 1.3) + 1.5 * sin(elapsed * 0.81 * w + 2.7))],
          ['ParamBodyAngleX', sway * (2.0 * sin(elapsed * 0.45 * w + 0.4) + 1.0 * sin(elapsed * 0.71 * w + 1.9))],
          ['ParamBodyAngleY', sway * (1.4 * sin(elapsed * 0.35 * w + 1.1) + 0.8 * sin(elapsed * 0.59 * w + 0.3))],
          ['ParamBodyAngleZ', sway * (1.0 * sin(elapsed * 0.55 * w + 2.1) + 0.6 * sin(elapsed * 0.83 * w + 1.5))],
        ];
        for (const [id, value] of idle) {
          try {
            core.addParameterValueById(id, value);
          } catch (err) {
            if (!missingParams.has(id)) {
              missingParams.add(id);
              console.warn(`[Live2D] failed to add param "${id}":`, err);
            }
          }
        }

        // 4. Re-bake with our overrides + idle additions for this frame.
        core.update();
        if (!firstApplyLogged) {
          firstApplyLogged = true;
          console.log('[Live2D] expression + idle loop is firing');
        }
        return result;
      };

      if (draggable) {
        model.buttonMode = true;
        const onDown = (e) => {
          model.dragging = true;
          model._pointerX = e.data.global.x - model.x;
          model._pointerY = e.data.global.y - model.y;
        };
        const onMove = (e) => {
          if (model.dragging) {
            model.position.x = e.data.global.x - model._pointerX;
            model.position.y = e.data.global.y - model._pointerY;
          }
        };
        const onUp = () => { model.dragging = false; };
        model.on('pointerdown', onDown);
        model.on('pointermove', onMove);
        model.on('pointerup', onUp);
        model.on('pointerupoutside', onUp);
      }

      if (clickToPlayMotion) {
        model.on('hit', () => {
          const groups = motionGroupsRef.current;
          const group = groups[0] ?? '';
          const defs = model.internalModel.motionManager.definitions[group] || [];
          if (defs.length) model.motion(group, Math.floor(Math.random() * defs.length));
        });
      }
    })();

    return () => {
      cancelled = true;
      const model = modelRef.current;
      if (app.resizeObserver) app.resizeObserver.disconnect();
      if (model) {
        model.removeAllListeners();
        modelRef.current = null;
      }
      // `removeView: true` (second positional arg) tells PIXI to remove the
      // <canvas> from the DOM as part of destroy.
      app.destroy(true, { children: true, texture: true, baseTexture: true });
      appRef.current = null;
    };
  }, [modelUrl, draggable, clickToPlayMotion]);

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%', height: '100%', ...style }} />
  );
});

export default Live2DCharacter;
