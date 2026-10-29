# -*- coding: utf-8 -*-
"""qwen_edit 模板单测：图结构、三路输出接线、参数收敛、提示词家族（全离线）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from comfy_agent.templates import catalog, get_template   # noqa: E402
from comfy_agent.templates.base import INPUT_FILE_PARAMS   # noqa: E402
from comfy_agent.templates.qwen import (                   # noqa: E402
    NEG_QWEN, QwenEdit, QWEN_PATCH, QWEN_TE, QWEN_UNET, QWEN_VAE)


def base_params(**kw):
    p = {"prompt": "把左下角的深色矩形改成亮绿色，红色圆保持原样",
         "image": "上传后.png", "resolution": 1024, "seed": 123}
    p.update(kw)
    return p


class TestQwenEditGraph(unittest.TestCase):
    def setUp(self):
        self.t = QwenEdit()
        self.g = self.t.render(base_params())

    def test_registered(self):
        self.assertIsNotNone(get_template("qwen_edit"))
        ids = [x["id"] for x in catalog()]
        self.assertIn("qwen_edit", ids)
        self.assertEqual(self.t.family, "qwen")

    def test_class_types(self):
        kinds = {n["class_type"] for n in self.g.values()}
        self.assertEqual(kinds, {
            "UNETLoader", "CLIPLoader", "VAELoader", "LoadImage",
            "ModelSamplingAuraFlow", "TextEncodeQwenImage21", "KSampler",
            "VAEDecode", "SaveImage"})

    def test_encoder_three_slots_feed_sampler(self):
        """TextEncodeQwenImage21 是 (positive, negative, latent) 三路，
        KSampler 必须分别接 0/1/2——接错就会退回"文生图"语义。"""
        self.assertEqual(self.g["7"]["inputs"]["positive"], ["6", 0])
        self.assertEqual(self.g["7"]["inputs"]["negative"], ["6", 1])
        self.assertEqual(self.g["7"]["inputs"]["latent_image"], ["6", 2])

    def test_reference_image_wiring(self):
        enc = self.g["6"]["inputs"]
        self.assertEqual(enc["images"], {"image_1": ["4", 0]})
        self.assertEqual(enc["vae"], ["3", 0])
        self.assertEqual(enc["clip"], ["2", 0])

    def test_loader_args(self):
        self.assertEqual(self.g["1"]["inputs"]["unet_name"], QWEN_UNET)
        self.assertEqual(self.g["1"]["inputs"]["weight_dtype"], "default")
        self.assertEqual(self.g["2"]["inputs"]["clip_name"], QWEN_TE)
        self.assertEqual(self.g["2"]["inputs"]["type"], "qwen_image")
        self.assertEqual(self.g["3"]["inputs"]["vae_name"], QWEN_VAE)

    def test_sampling_defaults(self):
        ins = self.g["7"]["inputs"]
        self.assertEqual((ins["steps"], ins["cfg"]), (25, 1.0))
        self.assertEqual((ins["sampler_name"], ins["scheduler"]),
                         ("res_multistep", "simple"))
        self.assertEqual(ins["seed"], 123)
        self.assertEqual(self.g["5"]["inputs"]["shift"], 3.0)
        self.assertEqual(self.g["5"]["inputs"]["sampling"], "flow")

    def test_prompt_passthrough(self):
        self.assertEqual(self.g["6"]["inputs"]["prompt"],
                         "把左下角的深色矩形改成亮绿色，红色圆保持原样")
        self.assertEqual(self.g["6"]["inputs"]["negative_prompt"], NEG_QWEN)
        self.assertEqual(self.g["9"]["inputs"]["filename_prefix"],
                         "agent_qwen_edit")


class TestQwenEditParams(unittest.TestCase):
    def test_resolution_clamped_to_32_grid(self):
        t = QwenEdit()
        cases = {1000: 992, 1024: 1024, 200: 512, 5000: 1536, 512: 512}
        for given, want in cases.items():
            g = t.render(base_params(resolution=given))
            self.assertEqual(g["6"]["inputs"]["resolution"], want, given)

    def test_seed_default_zero(self):
        g = QwenEdit().render(base_params(seed=None))
        self.assertEqual(g["7"]["inputs"]["seed"], 0)

    def test_choice_fallback_keeps_template_recommendation(self):
        """大脑常把采样器+调度器拼成一个串，应收敛到模板推荐值。"""
        t = QwenEdit()
        out, notes = t.normalize_params(base_params(sampler="dpmpp_2m_karras"))
        self.assertEqual(out["sampler"], "res_multistep")
        self.assertTrue(any("sampler" in n for n in notes))

    def test_input_file_declared(self):
        self.assertEqual(INPUT_FILE_PARAMS["qwen_edit"], [("image", "image")])
        self.assertEqual(QwenEdit().input_files, [("image", "image")])

    def test_missing_models(self):
        class K:
            def __init__(self, have):
                self.have = have

            def find_model(self, name, folders=None):
                return [name] if name in self.have else []

        t = QwenEdit()
        self.assertEqual(t.missing_models(K(t.models_used)), [])
        self.assertEqual(t.missing_models(K([QWEN_UNET, QWEN_VAE])), [QWEN_TE])

    def test_to_dict_exposes_params(self):
        d = QwenEdit().to_dict()
        names = [p["name"] for p in d["params"]]
        for n in ("prompt", "image", "resolution", "steps", "cfg", "shift"):
            self.assertIn(n, names)
        self.assertEqual(d["family"], "qwen")
        img = next(p for p in d["params"] if p["name"] == "image")
        self.assertTrue(img["default"] == "")


class TestQwenPromptFamily(unittest.TestCase):
    def test_family_guide_is_instruction_style(self):
        from comfy_agent import promptspec
        g = promptspec.guide_for("qwen")
        self.assertIn("指令", g["lang"])
        self.assertTrue(promptspec.negative_for("qwen"))
        self.assertIn("qwen", promptspec._NL_FAMILIES)
        self.assertIn("模糊", promptspec._NEG_KEYS["qwen"])

    def test_unknown_family_still_falls_back(self):
        from comfy_agent import promptspec
        self.assertEqual(promptspec.guide_for("nope")["lang"],
                         promptspec.FAMILY_GUIDES["sdxl"]["lang"])

    def test_check_prompt_runs(self):
        from comfy_agent import promptspec
        r = promptspec.check_prompt(
            "qwen", "把左下角的矩形改成亮绿色，其余保持不变", NEG_QWEN)
        self.assertIsInstance(r, dict)

    def test_no_video_motion_warning(self):
        """改图指令不是视频：不许提示"缺动作/镜头词，视频会变静帧"。
        实跑踩过：端到端 warnings 里冒出视频专用警告。"""
        from comfy_agent import promptspec
        for text in ("把左下角的矩形改成亮绿色，其余保持不变",
                     "Change the circle to bright blue, keep the rest"):
            warns = promptspec.check_prompt("qwen", text, "")["warnings"]
            self.assertFalse([w for w in warns if "镜头" in w or "静帧" in w],
                             (text, warns))

    def test_no_block_coverage_for_qwen(self):
        from comfy_agent import promptspec
        self.assertEqual(promptspec.block_coverage("qwen", "make it blue"),
                         {"present": [], "missing": [], "suggest": {}})

    def test_empty_negative_not_warned(self):
        """用户没写负面时补默认是正常行为，不该报"未包含默认项"。"""
        from comfy_agent import promptspec
        _, neg, warns = promptspec.prepare(
            "qwen", "把矩形改成亮绿色", "")
        self.assertIn("模糊", neg)          # 默认负面照旧合进来
        self.assertFalse([w for w in warns if "默认负面" in w], warns)
        # 用户写了负面但漏关键项 → 仍然提示
        _, _, warns2 = promptspec.prepare("qwen", "改亮绿色", "潦草")
        self.assertTrue([w for w in warns2 if "默认负面" in w])


class TestQwenBatchAndRegion(unittest.TestCase):
    """一指令多图（RepeatLatentBatch）与局部改图（裁剪→重绘→贴回）。"""

    def test_batch_one_keeps_original_graph(self):
        g = QwenEdit().render(base_params())
        self.assertNotIn("11", g)
        self.assertEqual(g["7"]["inputs"]["latent_image"], ["6", 2])

    def test_batch_repeats_latent(self):
        g = QwenEdit().render(base_params(batch=4))
        self.assertEqual(g["11"]["class_type"], "RepeatLatentBatch")
        self.assertEqual(g["11"]["inputs"]["samples"], ["6", 2])
        self.assertEqual(g["11"]["inputs"]["amount"], 4)
        self.assertEqual(g["7"]["inputs"]["latent_image"], ["11", 0])
        self.assertEqual(g["9"]["inputs"]["images"], ["8", 0])

    def test_batch_clamped_to_1_4(self):
        g = QwenEdit().render(base_params(batch=99))
        self.assertEqual(g["11"]["inputs"]["amount"], 4)
        g2 = QwenEdit().render(base_params(batch=0))
        self.assertNotIn("11", g2)

    def test_region_crops_and_pastes_back(self):
        g = QwenEdit().render(base_params(region="100,300,256,256"))
        self.assertEqual(g["10"]["class_type"], "ImageCrop")
        self.assertEqual(g["10"]["inputs"],
                         {"image": ["4", 0], "width": 256, "height": 256,
                          "x": 100, "y": 300})
        self.assertEqual(g["6"]["inputs"]["images"], {"image_1": ["10", 0]})
        self.assertEqual(g["12"]["class_type"], "ImageScale")
        self.assertEqual((g["12"]["inputs"]["width"],
                          g["12"]["inputs"]["height"]), (256, 256))
        self.assertEqual(g["12"]["inputs"]["image"], ["8", 0])
        self.assertEqual(g["13"]["class_type"], "ImageCompositeMasked")
        self.assertEqual(g["13"]["inputs"]["destination"], ["4", 0])
        self.assertEqual(g["13"]["inputs"]["source"], ["12", 0])
        # resize_source 必须 False：True 会把 source 缩放到整张图尺寸，几何全错
        self.assertFalse(g["13"]["inputs"]["resize_source"])
        self.assertEqual(g["9"]["inputs"]["images"], ["13", 0])

    def test_region_auto_resolution_covers_crop(self):
        g = QwenEdit().render({"prompt": "改", "image": "a.png",
                               "region": "0,0,300,200"})
        self.assertEqual(g["6"]["inputs"]["resolution"], 512)   # 300→向上到 320→夹到 512
        g2 = QwenEdit().render({"prompt": "改", "image": "a.png",
                                "region": "0,0,900,700"})
        self.assertEqual(g2["6"]["inputs"]["resolution"], 928)

    def test_region_respects_explicit_resolution(self):
        g = QwenEdit().render(base_params(region="0,0,300,200", resolution=1024))
        self.assertEqual(g["6"]["inputs"]["resolution"], 1024)

    def test_region_forces_single_output(self):
        """贴回是按 1 张算的，多张会互相覆盖。"""
        g = QwenEdit().render(base_params(region="0,0,128,128", batch=4))
        self.assertNotIn("11", g)

    def test_bad_region_falls_back_to_full_image(self):
        for bad in ("", "abc", "1,2,3", "0,0,-1,64", "0,0,64,0", None):
            g = QwenEdit().render(base_params(region=bad))
            self.assertNotIn("10", g, bad)
            self.assertEqual(g["6"]["inputs"]["images"], {"image_1": ["4", 0]})

    def test_region_accepts_cn_comma(self):
        g = QwenEdit().render(base_params(region="100，300，256，256"))
        self.assertEqual(g["10"]["inputs"]["x"], 100)

    def test_to_dict_lists_new_params(self):
        names = [p["name"] for p in QwenEdit().to_dict()["params"]]
        self.assertIn("region", names)
        self.assertIn("batch", names)
        self.assertIn("structure", names)


class TestQwenStructureControl(unittest.TestCase):
    """结构控制：必须喂 Canny 边缘图，喂 RGB 原图实测花屏。"""

    def test_off_by_default(self):
        g = QwenEdit().render(base_params())
        for n in ("20", "21", "22"):
            self.assertNotIn(n, g)
        self.assertEqual(g["7"]["inputs"]["model"], ["5", 0])

    def test_canny_chain_wired(self):
        g = QwenEdit().render(base_params(structure="canny"))
        self.assertEqual(g["20"]["class_type"], "Canny")
        self.assertEqual(g["20"]["inputs"]["image"], ["4", 0])
        self.assertEqual(g["21"]["class_type"], "ModelPatchLoader")
        self.assertEqual(g["21"]["inputs"]["name"], QWEN_PATCH)
        self.assertEqual(g["22"]["class_type"], "QwenImageDiffsynthControlnet")
        ins = g["22"]["inputs"]
        self.assertEqual(ins["image"], ["20", 0])       # 边缘图，不是原图
        self.assertEqual((ins["start_percent"], ins["end_percent"]), (0.0, 1.0))
        self.assertEqual(g["7"]["inputs"]["model"], ["22", 0])

    def test_canny_uses_crop_when_region(self):
        g = QwenEdit().render(base_params(structure="canny", region="0,0,128,128"))
        self.assertEqual(g["20"]["inputs"]["image"], ["10", 0])

    def test_strength_default_and_override(self):
        self.assertEqual(
            QwenEdit().render(base_params(structure="canny"))["22"]["inputs"]["strength"],
            0.7)
        self.assertEqual(
            QwenEdit().render(base_params(structure="canny",
                                          structure_strength=1.2))["22"]["inputs"]["strength"],
            1.2)

    def test_patch_only_required_in_canny_mode(self):
        """选 off 的用户不该被要求下载 controlnet 补丁。"""
        t = QwenEdit()
        self.assertNotIn(QWEN_PATCH, t.models_used_for({}))
        self.assertNotIn(QWEN_PATCH, t.models_used_for({"structure": "off"}))
        self.assertIn(QWEN_PATCH, t.models_used_for({"structure": "canny"}))
        self.assertNotIn(QWEN_PATCH, t.models_used)

    def test_unknown_structure_value_falls_back_to_off(self):
        out, notes = QwenEdit().normalize_params(base_params(structure="canny-ish"))
        self.assertEqual(out["structure"], "off")
        self.assertTrue(any("structure" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
