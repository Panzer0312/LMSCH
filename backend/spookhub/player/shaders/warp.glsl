// Spookhub output warp: 4-corner keystone, flip, edge mask with feather.
// Parameters are set at runtime via glsl-shader-opts (vo=gpu-next). All zero = identity.

//!PARAM c0x
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c0y
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c1x
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c1y
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c2x
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c2y
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c3x
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM c3y
//!TYPE float
//!MINIMUM -0.5
//!MAXIMUM 0.5
0.0

//!PARAM mask_t
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 0.5
0.0

//!PARAM mask_r
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 0.5
0.0

//!PARAM mask_b
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 0.5
0.0

//!PARAM mask_l
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 0.5
0.0

//!PARAM feather
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 0.5
0.0

//!PARAM flip_h
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 1
0.0

//!PARAM flip_v
//!TYPE float
//!MINIMUM 0
//!MAXIMUM 1
0.0

//!HOOK OUTPUT
//!BIND HOOKED
//!DESC spookhub warp

// Homography mapping the unit square onto the quad TL, TR, BR, BL (Heckbert's square-to-quad).
// Columns hold (a d g), (b e h), (c f 1) so that M * (u, v, 1) = (x*w, y*w, w).
mat3 square_to_quad(vec2 p0, vec2 p1, vec2 p2, vec2 p3) {
    vec2 s = p0 - p1 + p2 - p3;
    vec2 d1 = p1 - p2;
    vec2 d2 = p3 - p2;
    float det = d1.x * d2.y - d2.x * d1.y;
    float g = 0.0;
    float h = 0.0;
    if (abs(det) > 1e-9) {
        g = (s.x * d2.y - d2.x * s.y) / det;
        h = (d1.x * s.y - s.x * d1.y) / det;
    }
    return mat3(
        p1.x - p0.x + g * p1.x, p1.y - p0.y + g * p1.y, g,
        p3.x - p0.x + h * p3.x, p3.y - p0.y + h * p3.y, h,
        p0.x, p0.y, 1.0);
}

// Adjugate is enough: the homogeneous divide cancels the determinant.
mat3 adjugate(mat3 m) {
    return mat3(
        m[1][1] * m[2][2] - m[2][1] * m[1][2],
        m[2][1] * m[0][2] - m[0][1] * m[2][2],
        m[0][1] * m[1][2] - m[1][1] * m[0][2],
        m[2][0] * m[1][2] - m[1][0] * m[2][2],
        m[0][0] * m[2][2] - m[2][0] * m[0][2],
        m[1][0] * m[0][2] - m[0][0] * m[1][2],
        m[1][0] * m[2][1] - m[2][0] * m[1][1],
        m[2][0] * m[0][1] - m[0][0] * m[2][1],
        m[0][0] * m[1][1] - m[1][0] * m[0][1]);
}

float edge(float lo, float v) {
    // Hard edge without feather; smoothstep(a, a, x) is undefined in GLSL.
    float f = max(feather, 1e-5);
    return smoothstep(lo, lo + f, v);
}

vec4 hook() {
    vec2 p = HOOKED_pos;
    vec2 q0 = vec2(0.0, 0.0) + vec2(c0x, c0y);
    vec2 q1 = vec2(1.0, 0.0) + vec2(c1x, c1y);
    vec2 q2 = vec2(1.0, 1.0) + vec2(c2x, c2y);
    vec2 q3 = vec2(0.0, 1.0) + vec2(c3x, c3y);
    vec3 r = adjugate(square_to_quad(q0, q1, q2, q3)) * vec3(p, 1.0);
    if (abs(r.z) < 1e-9)
        return vec4(0.0, 0.0, 0.0, 1.0);
    vec2 uv = r.xy / r.z;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)
        return vec4(0.0, 0.0, 0.0, 1.0);
    // Mask edges follow the corrected picture, so they line up with the keystoned surface.
    float m = edge(mask_l, uv.x) * edge(mask_r, 1.0 - uv.x)
            * edge(mask_t, uv.y) * edge(mask_b, 1.0 - uv.y);
    if (flip_h > 0.5)
        uv.x = 1.0 - uv.x;
    if (flip_v > 0.5)
        uv.y = 1.0 - uv.y;
    vec4 c = HOOKED_tex(uv);
    return vec4(c.rgb * m, c.a);
}
