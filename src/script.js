const localModel = "haru_greeter_pro_jp/runtime/haru_greeter_t05.model3.json";
const live2d = PIXI.live2d;

(async function main() {
  const app = new PIXI.Application({
    view: document.getElementById("canvas"),
    autoStart: true,
    resizeTo: window,
    backgroundColor: 0x000000
  });

  const model = await live2d.Live2DModel.from(localModel);
  app.stage.addChild(model);

  // Layout & Initial Scaling
  const baseScale = Math.min((innerWidth * 0.9) / model.width, (innerHeight * 0.9) / model.height);
  model.scale.set(baseScale);
  model.y = (innerHeight - model.height) / 2;
  model.x = (innerWidth - model.width) / 2;

  draggable(model);

  // --- ZOOM LOGIC ---
  const canvas = document.getElementById("canvas");
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    
    // Zoom speed factor
    const zoomSpeed = 0.001;
    let newScale = model.scale.x - e.deltaY * zoomSpeed * model.scale.x;
    
    // Clamp zoom between 0.1x and 5x of the base scale
    const minScale = baseScale * 0.2;
    const maxScale = baseScale * 10;
    newScale = Math.max(minScale, Math.min(maxScale, newScale));
    
    model.scale.set(newScale);
  }, { passive: false });

  const controlDiv = document.getElementById("control");
  const progressInner = document.getElementById("progress-inner");

  // --- 1. MOTIONS ---
  const motionNames = Object.keys(model.internalModel.motionManager.definitions[""] || {});
  if (motionNames.length > 0) {
    const section = document.createElement("div");
    section.innerHTML = "<h3>Motions</h3>";
    motionNames.forEach((name, index) => {
      const btn = document.createElement("button");
      btn.innerText = `Motion ${index.toString().padStart(2, '0')}`;
      btn.onclick = () => {
        model.motion("", index);
        const motionDef = model.internalModel.motionManager.definitions[""][index];
        model.currentMotionDuration = motionDef.Duration || 5;
      };
      section.appendChild(btn);
    });
    controlDiv.appendChild(section);
  }

  // --- 2. EXPRESSIONS ---
  const expressionParams = [
    { id: "ParamMouthOpenY", name: "Mouth Open", min: 0, max: 1, val: 0 },
    { id: "ParamMouthForm", name: "Mouth Form", min: -1, max: 1, val: 0 },
    { id: "ParamTere", name: "Blush", min: 0, max: 1, val: 0 },
    { id: "ParamTear", name: "Tears", min: 0, max: 1, val: 0 },
    { id: "ParamEyeLSmile", name: "Smile L", min: 0, max: 1, val: 0 },
    { id: "ParamEyeRSmile", name: "Smile R", min: 0, max: 1, val: 0 },
    { id: "ParamBrowLY", name: "Brow L (Y)", min: -1, max: 1, val: 0 },
    { id: "ParamBrowRY", name: "Brow R (Y)", min: -1, max: 1, val: 0 },
    { id: "ParamEyeBallForm", name: "Pupil Size", min: -1, max: 1, val: 0 }
  ];

  const expSection = document.createElement("div");
  expSection.innerHTML = "<h3 style='margin-top:20px'>Facial Control</h3>";
  
  expressionParams.forEach(p => {
    const container = document.createElement("div");
    container.style.marginBottom = "12px";
    container.innerHTML = `
      <div style="display:flex; justify-content:space-between">
        <label style="font-size:11px; color:#aaa">${p.name}</label>
        <span class="val-display" style="font-size:10px; color:#00ffcc">0</span>
      </div>
      <input type="range" min="${p.min}" max="${p.max}" step="0.01" value="${p.val}" style="width:100%">
    `;
    const slider = container.querySelector("input");
    const display = container.querySelector(".val-display");
    
    slider.oninput = () => {
      p.val = parseFloat(slider.value);
      display.innerText = p.val.toFixed(2);
    };
    expSection.appendChild(container);
  });
  controlDiv.appendChild(expSection);

  model.on("beforeModelUpdate", () => {
    expressionParams.forEach(p => {
      model.internalModel.coreModel.setParameterValueById(p.id, p.val);
    });
  });

  app.ticker.add(() => {
    const mm = model.internalModel.motionManager;
    if (mm.playing) {
        const elapsed = mm.state.time % (model.currentMotionDuration || 5);
        const progress = elapsed / (model.currentMotionDuration || 5);
        progressInner.style.width = Math.min(progress * 100, 100) + "%";
    } else {
        progressInner.style.width = "0%";
    }
  });

  model.on("hit", (hitAreas) => {
    const randomIndex = Math.floor(Math.random() * motionNames.length);
    model.motion("", randomIndex);
    const motionDef = model.internalModel.motionManager.definitions[""][randomIndex];
    model.currentMotionDuration = motionDef.Duration || 5;
  });

})();

function draggable(model) {
  model.buttonMode = true;
  model.on("pointerdown", e => {
    model.dragging = true;
    model._pointerX = e.data.global.x - model.x;
    model._pointerY = e.data.global.y - model.y;
  });
  model.on("pointermove", e => {
    if (model.dragging) {
      model.position.x = e.data.global.x - model._pointerX;
      model.position.y = e.data.global.y - model._pointerY;
    }
  });
  model.on("pointerupoutside", () => model.dragging = false);
  model.on("pointerup", () => model.dragging = false);
}
