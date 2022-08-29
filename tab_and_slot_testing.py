#Author-
#Description-

import adsk.core, adsk.fusion, adsk.cam, traceback

def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui  = app.userInterface

        des = adsk.fusion.Design.cast(app.activeProduct)
        root = des.rootComponent

    
        # ============================= SETTINGS ============================= #
        hole_diameter = 0.41
        hole_depth = 2.005

        tab_width = 0.825
        tab_spacing = 2.516  # center to center

        nut_width = 0.85
        nut_height = 0.31
        nut_offset = 0.295
        # ==================================================================== #


        # select a point which defines where to cut out the tab and slot geometry
        # this should be the point to add the screw hole in the top plate (same as selecting the points to add a hole in the usual hole feature)
        selections = ui.activeSelections
        selected_points = []
        for selection in selections:
            if selection.entity.objectType == adsk.fusion.SketchPoint.classType():
                selected_points.append(selection.entity)

        for selected_point in selected_points:
            point = selected_point.worldGeometry
            face = get_face_under_point(point)
            top_param_body = face.body

            # get the normal and reverse its direction so it points into the body
            normal: adsk.core.Vector3D = get_planar_face_normal(face)
            normal.scaleBy(-1.0)

            # cast a ray to find the back face of the body
            hit_collection: adsk.core.ObjectCollection = adsk.core.ObjectCollection.create()
            obj_collection = root.findBRepUsingRay(point, normal, 1, 1e-5, False, hit_collection)

            # get the hit face that corresponds to the back face
            for obj, hit in zip(obj_collection, hit_collection):
                if obj.body == top_param_body and obj != face:
                    hit_point = hit
                    back_face = obj
                    break
            top_sheet_thickness = hit_point.distanceTo(point)

            # now cast the ray to find the bottom body for the joinery
            obj_collection = root.findBRepUsingRay(hit_point, normal, 0, 1e-5, False, hit_collection)
            bottom_param_body = obj_collection.item(0)
            normal.scaleBy(-1.0)  # fix the normal vector
            laser_face = get_face_to_cut_body(bottom_param_body)
            bottom_sheet_thickness = get_2d_body_thickness(laser_face)
            z_dir = get_planar_face_normal(laser_face)
            x_dir = normal.crossProduct(z_dir)

            # create the bodies which represent the tab and screw cutouts
            tBRep: adsk.fusion.TemporaryBRepManager = adsk.fusion.TemporaryBRepManager.get()
            bodies = []

            # cylinder for screw hole in top sheet
            center_body = tBRep.createCylinderOrCone(adsk.core.Point3D.create(0, 0, 0), hole_diameter / 2, adsk.core.Point3D.create(0, -top_sheet_thickness, 0), hole_diameter / 2)

            # box for screw hole in bottom sheet
            tBRep.booleanOperation(center_body, tBRep.createBox(adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(0, (top_sheet_thickness - hole_depth) / 2 - top_sheet_thickness, 0), adsk.core.Vector3D.create(0, 1, 0), adsk.core.Vector3D.create(1, 0, 0), hole_depth - top_sheet_thickness, hole_diameter, bottom_sheet_thickness)), adsk.fusion.BooleanTypes.UnionBooleanType)

            # box for nut cutout in bottom sheet
            tBRep.booleanOperation(center_body, tBRep.createBox(adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(0, -hole_depth + nut_height / 2 + nut_offset, 0), adsk.core.Vector3D.create(0, 1, 0), adsk.core.Vector3D.create(1, 0, 0), nut_height, nut_width, bottom_sheet_thickness)), adsk.fusion.BooleanTypes.UnionBooleanType)

            # box for left tab (looking from front)
            left_tab_body = tBRep.createBox(adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(-tab_spacing / 2, -top_sheet_thickness / 2, 0), adsk.core.Vector3D.create(0, 1, 0), adsk.core.Vector3D.create(1, 0, 0), top_sheet_thickness, tab_width, bottom_sheet_thickness))

            # box for right tab
            right_tab_body = tBRep.createBox(adsk.core.OrientedBoundingBox3D.create(adsk.core.Point3D.create(tab_spacing / 2, -top_sheet_thickness / 2, 0), adsk.core.Vector3D.create(0, 1, 0), adsk.core.Vector3D.create(1, 0, 0), top_sheet_thickness, tab_width, bottom_sheet_thickness))

            # transform the tool bodies onto the part
            transform = adsk.core.Matrix3D.create()
            transform.setWithCoordinateSystem(selected_point.worldGeometry, x_dir, normal, z_dir)
            tBRep.transform(center_body, transform)
            tBRep.transform(left_tab_body, transform)
            tBRep.transform(right_tab_body, transform)

            # add the temporary bodies to a base feature
            top_comp = top_param_body.parentComponent
            top_base_feature = top_comp.features.baseFeatures.add()
            top_base_feature.startEdit()
            top_comp.bRepBodies.add(center_body, top_base_feature)
            top_comp.bRepBodies.add(left_tab_body, top_base_feature)
            top_comp.bRepBodies.add(right_tab_body, top_base_feature)
            top_base_feature.finishEdit()

            # create a combine feature to subtract the tab and slot bodies from the top part
            tool_bodies = adsk.core.ObjectCollection.create()
            tool_bodies.add(top_base_feature.bodies.item(0))
            tool_bodies.add(top_base_feature.bodies.item(1))
            tool_bodies.add(top_base_feature.bodies.item(2))
            combine_input = top_comp.features.combineFeatures.createInput(top_param_body, tool_bodies)
            combine_input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
            combine_feature = top_comp.features.combineFeatures.add(combine_input)

            # add the temporary bodies to a base feature
            bottom_comp = bottom_param_body.parentComponent
            bottom_base_feature = bottom_comp.features.baseFeatures.add()
            bottom_base_feature.startEdit()
            bottom_comp.bRepBodies.add(center_body, bottom_base_feature)
            bottom_comp.bRepBodies.add(left_tab_body, bottom_base_feature)
            bottom_comp.bRepBodies.add(right_tab_body, bottom_base_feature)
            bottom_base_feature.finishEdit()

            # create two combine features for the bottom part
            tool_bodies_cut = adsk.core.ObjectCollection.create()
            tool_bodies_cut.add(bottom_base_feature.bodies.item(0))
            tool_bodies_add = adsk.core.ObjectCollection.create()
            tool_bodies_add.add(bottom_base_feature.bodies.item(1))
            tool_bodies_add.add(bottom_base_feature.bodies.item(2))

            combine_input = bottom_comp.features.combineFeatures.createInput(bottom_param_body, tool_bodies_cut)
            combine_input.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
            combine_feature = bottom_comp.features.combineFeatures.add(combine_input)

            combine_input = bottom_comp.features.combineFeatures.createInput(bottom_param_body, tool_bodies_add)
            combine_input.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
            combine_feature = bottom_comp.features.combineFeatures.add(combine_input)

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))


# Get the face the selected point lies on. This assumes the point is
# in root component space. The returned face will be in the context
# of the root component.
#
# There is a case where more than one face can be found but in this case
# None is returned. The case is when the point is very near the edge of
# the face so it is ambiguous which face the point is on.
def get_face_under_point(point: adsk.core.Point3D) -> adsk.fusion.BRepFace:
    app = adsk.core.Application.get()
    des: adsk.fusion.Design = app.activeProduct
    root = des.rootComponent

    found_faces: adsk.core.ObjectCollection = root.findBRepUsingPoint(point, adsk.fusion.BRepEntityTypes.BRepFaceEntityType, 0.01, True)
    if found_faces.count == 0:
        return None
    else:
        face: adsk.fusion.BRepFace = found_faces.item(0)
        return face

    return None


def get_planar_face_normal(face) -> adsk.core.Vector3D:
    # use a surfaceEvaluator so the normal is guaranteed to point outward from the body
    success, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    if success:
        return normal
    else:
        return adsk.core.Vector3D.create() # return a default vector if fails


# given a brepbody, determine which face has the 2D profile that could be cut out to produce the body
def get_face_to_cut_body(body) -> adsk.fusion.BRepFace:
    # for now, just get the planar face with largest area
    faces = body.faces
    sorted_faces = [face for face in faces]
    sorted_faces.sort(key = lambda f: f.area, reverse = True)

    for face in sorted_faces:
        if face.geometry.surfaceType == 0:
            return face


# pass the face to laser cut from and get the expected thickness of the cut
def get_2d_body_thickness(face):
    app = adsk.core.Application.get()
    des = adsk.fusion.Design.cast(app.activeProduct)
    root: adsk.fusion.Component = adsk.fusion.Component.cast(des.rootComponent)

    # cast a ray looking for faces
    normal: adsk.core.Vector3D = get_planar_face_normal(face)
    normal.scaleBy(-1.0)
    point = face.pointOnFace
    hit_collection: adsk.core.ObjectCollection = adsk.core.ObjectCollection.create()
    obj_collection = root.findBRepUsingRay(point, normal, 1, 1e-5, False, hit_collection)

    # look for a detected face that belongs to the same body but isn't the starting face
    for obj, hit in zip(obj_collection, hit_collection):
        if obj.body == face.body and obj != face:
            return hit.distanceTo(point)

    # return zero if we don't find the opposite face
    return 0.0
