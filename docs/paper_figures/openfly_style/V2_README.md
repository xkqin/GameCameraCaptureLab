# OpenFly-calibrated V2 figures

This set adds three figures without overwriting the earlier six.

- Figure 07 is a full-bleed teaser that combines real internet-video frames,
  public 3D-scene imagery, and original controllable game-world concepts.
- Figure 08 is an image-led framework for internet video, public 3D scenes,
  and game-camera acquisition.
- Figure 09 shows three real SpatialVID-HQ samples with their estimated pose
  paths and annotation dimensions.

Routes, camera marks, labels, charts, and framework geometry remain editable
SVG vectors. Raster panels are either real dataset samples or clearly
identified generated concept scenes.

SpatialVID-HQ is gated on Hugging Face and uses CC BY-NC-SA 4.0. Its camera
pose is labelled as estimated annotation, not sensor ground truth.

## Rebuild

Run these commands from this directory:

    python build_openfly_v2_figures.py
    python render_openfly_v2_figures.py
    python verify_openfly_v2_outputs.py
