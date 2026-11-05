Box stand-ins for KiCad's stock 3D models (#757). Tests resolve `${KICAD<n>_3DMODEL_DIR}` here
(tests/conftest.py), so check_body_clash judges the same bodies on every host.
Regenerate: `python3 -I tests/fixtures/3dmodels/make_boxes.py <share/kicad/3dmodels>`.
Boxes are derived from KiCad's library (CC-BY-SA 4.0 with the KiCad libraries exception); the
PinHeader files are the library's STEP files gzipped, as check_model_fit reads pins from the .step.
